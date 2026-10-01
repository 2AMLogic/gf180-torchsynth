// one_shot_voice_top.sv -- the integrated WHOLE-VOICE one-shot top (issue
// #79): every landed Epic #2 RTL engine composed into one clip renderer.
//
//   6x adsr_engine (#70)  --+
//                            +--> 2x lfo_vca_engine (#71) --+
//                            |                               |
//                            +-------------------------------+--> mod_matrix_engine (#72)
//                                                                     |
//                                            5x upsample_engine (#72) <+ (column load)
//                                                     |
//                 +-----------------------------------+------------------+
//                 |                 |                                    |
//        sine_vco_engine (#73)  square_saw_vco_engine (#74)   (amp columns)
//                 |                 |        noise_stream_dut (#75)      |
//                 +-----------------+----------------+-------------------+
//                                                    |
//                                        audio_mix_engine (#76)
//                                                    |  (the link seam)
//                                      normalization_replay_engine (#77)
//
// This is the DR-0010 "Normalization replay controller + one-shot top" owner
// row (#77) carried to its whole-voice conclusion: the sources, the control
// path, the control/audio-rate crossing, the VCAs, the mixer and the two-pass
// replay are all resident RTL here. Compare with ``one_shot_tail_top.sv``,
// which composes only the #76 -> #77 tail over host-fed source/amplitude
// streams.
//
// What the host still supplies, and why (stated rather than implied):
//
//   - **The declared shadow words.** DR-0010's 2026-09-22 amendment (issue
//     #74 remainder, tracking #172) ratifies the transcendental
//     sub-expressions -- ``exp2`` on both pitch paths, ``partials_constant``,
//     ``tanh``, the ADSR ``**alpha`` powers and the LFO shape weights -- as
//     **host-replayed deterministic words at the declared shadow boundary**.
//     They arrive here as per-tick / per-sample streams exactly as each
//     engine's own declared interface takes them. That is the ratified
//     architecture, not a gap in this composition.
//   - **The S1 entry words** (quantized patch parameters) and the exact C8
//     noise byte stream: DR-0003's host boundary.
//   - **Phase sequencing.** ``ctl_*`` / ``audio_*`` enables and the replay
//     engine's ``start`` / ``mix_done``. No clip buffer exists anywhere in
//     this top (DR-0010 P4): pass 2 is a complete re-render of the audio
//     chain over the retained control columns and a re-streamed noise clip.
//
// What this top does NOT contain:
//
//   - the serialized single-MAC schedule (DR-0010 P1/P3 schedule-candidate):
//     every engine still retires one sample/tick per *enabled* cycle, and the
//     audio group below is four cycles wide only because the noise lane's
//     byte port is 8 bits;
//   - the #69 protocol receiver (see ``render_binding_top.sv`` for the
//     receiver/replay binding, a separate declared composition);
//   - any synthesis, layout, signoff, hardware-playback or sound-fidelity
//     claim. None is made or implied.
//
// === Phase contract ===
//
// Control walk (1,764 ticks, four cycles each; the host drives the enables):
//   p0  ctl_en_adsr : the six envelope engines retire tick t
//   p1  ctl_en_lfo  : the two LFO/control-VCA engines retire tick t, reading
//                     the envelope engines' registered outputs for tick t
//   p2  ctl_en_mm   : the matrix retires tick t, reading adsr_1/adsr_2 and
//                     both post-control-VCA columns for tick t
//   p3  ctl_load    : the matrix's five registered route words are written
//                     into the five upsample column memories at index t
//
// Audio walk (one group of four cycles per sample, per pass):
//   a0  up_en   : the five upsample engines emit sample j
//   a1  vco_en  : both VCOs consume up[j] and emit vco_1/vco_2 raw[j]
//   a2  mix_en  : the mixer consumes raw[j], the noise sample[j] and the
//                 three amplitude columns up[j], emitting post_vca/mix[j]
//   a3          : the replay engine consumes mix[j] on the link seam
// One noise byte is fed per cycle, so each four-cycle group assembles the
// NEXT sample while the mixer consumes the current one (the host primes four
// bytes before the first group).
//
// === Named internal seams (mutation targets; the flow plants faults here) ===
//
//   col_adsr_1/2, col_lfo_1/2 : matrix source-column binding
//   lfo_rate_env/lfo_gain     : which envelope drives which LFO role
//   up_pitch_vco_1/2          : pitch-column binding
//   amp_vco_1/amp_vco_2/amp_noise : amplitude-column binding
//   raw_noise_bound           : the noise lane's sample into the mixer
//   link_valid                : the mixer -> replay sample stream
//
// PDK-free plain SystemVerilog for Icarus Verilog (-g2012): structural only,
// no interfaces, no classes, no vendor or PDK cells.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module one_shot_voice_top (
    input  wire clk,
    input  wire rst,

    // ---- control-domain sequencing --------------------------------------
    input  wire        ctl_trigger,      // with all three ctl enables high
    input  wire        ctl_en_adsr,
    input  wire        ctl_en_lfo,
    input  wire        ctl_en_mm,
    input  wire        ctl_load,         // matrix -> upsample column write
    input  wire [10:0] ctl_load_index,

    // ---- ADSR formation words (6 instances, packed in prefix order:
    //      adsr_1, adsr_2, lfo_1_rate, lfo_2_rate, lfo_1_amp, lfo_2_amp) ---
    input  wire [6*47-1:0]         adsr_duration_q,
    input  wire [6*47-1:0]         adsr_attack_q,
    input  wire [6*47-1:0]         adsr_decay_q,
    input  wire [6*47-1:0]         adsr_release_q,
    input  wire [5:0]              adsr_duration_zero,
    input  wire [5:0]              adsr_attack_zero,
    input  wire [5:0]              adsr_decay_zero,
    input  wire [5:0]              adsr_release_zero,
    input  wire [6*C4_WIDTH-1:0]   adsr_sustain_entry,
    input  wire signed [62:0]      adsr_eps60,
    input  wire [6*32-1:0]         adsr_shadow_attack,
    input  wire [6*32-1:0]         adsr_shadow_decay,
    input  wire [6*32-1:0]         adsr_shadow_release,
    output wire [6*C1_WIDTH-1:0]   adsr_env,
    output wire [5:0]              adsr_valid,
    output wire [6*32-1:0]         adsr_op_mults,
    output wire [6*32-1:0]         adsr_op_narrows,
    output wire [6*32-1:0]         adsr_op_shadow,
    output wire [6*32-1:0]         adsr_op_rampdivs,

    // ---- LFO + control-rate VCA (2 instances: lfo_1, lfo_2) -------------
    input  wire [2*C4_WIDTH-1:0]   lfo_freq,
    input  wire [2*C4_WIDTH-1:0]   lfo_depth,
    input  wire [2*C2_WIDTH-1:0]   lfo_init,
    input  wire [2*5*32-1:0]       lfo_weights,   // per side: sin,tri,saw,rsaw,sqr
    output wire [2*C1_WIDTH-1:0]   lfo_raw,
    output wire [2*C1_WIDTH-1:0]   lfo_post,
    output wire [1:0]              lfo_valid,
    output wire [2*32-1:0]         lfo_op_mults,
    output wire [2*32-1:0]         lfo_op_narrows,
    output wire [2*32-1:0]         lfo_op_phases,
    output wire [2*32-1:0]         lfo_op_clamps,

    // ---- modulation matrix (4 sources x 5 routes) -----------------------
    input  wire [4*C4_WIDTH-1:0]   depths_r0,     // vco_1_pitch
    input  wire [4*C4_WIDTH-1:0]   depths_r1,     // vco_1_amp
    input  wire [4*C4_WIDTH-1:0]   depths_r2,     // vco_2_pitch
    input  wire [4*C4_WIDTH-1:0]   depths_r3,     // vco_2_amp
    input  wire [4*C4_WIDTH-1:0]   depths_r4,     // noise_amp
    output wire [5*C1_WIDTH-1:0]   mm_out,
    output wire                    mm_valid,
    output wire [31:0]             mm_op_mults,
    output wire [31:0]             mm_op_adds,
    output wire [31:0]             mm_op_narrows,
    output wire [31:0]             mm_op_sats,

    // ---- audio-domain sequencing ----------------------------------------
    input  wire        audio_trigger,    // per pass: re-arm the audio engines
    input  wire        audio_go,         // per pass: start the upsample walks
    input  wire        up_en,
    input  wire        vco_en,
    input  wire        mix_en,

    // ---- endpoint-aligned upsample (5 columns) --------------------------
    output wire [5*C1_WIDTH-1:0]   up_word,
    output wire [4:0]              up_valid,
    output wire [5*32-1:0]         up_out_count,
    output wire [5*32-1:0]         up_op_mults,
    output wire [5*32-1:0]         up_op_adds,
    output wire [5*32-1:0]         up_op_narrows,
    output wire [5*32-1:0]         up_op_frac_words,
    output wire [5*32-1:0]         up_op_coord_steps,
    output wire [5*32-1:0]         up_op_sats,

    // ---- vco_1 (sine) ---------------------------------------------------
    input  wire [C4_WIDTH-1:0]         midi_f0_word,
    input  wire [C4_WIDTH-1:0]         vco1_tuning_word,
    input  wire [C4_WIDTH-1:0]         vco1_depth_word,
    input  wire [C2_WIDTH-1:0]         vco1_init_phase_word,
    input  wire signed [C3_WIDTH-1:0]  vco1_fq_word,      // declared exp2 shadow
    output wire signed [C1_WIDTH-1:0]  vco1_word,
    output wire [C2_WIDTH-1:0]         vco1_phase_out,
    output wire                        vco1_valid,
    output wire [31:0] vco1_op_mults, vco1_op_adds, vco1_op_narrows,
    output wire [31:0] vco1_op_shadows, vco1_op_sats, vco1_op_clamps,

    // ---- vco_2 (square/saw) ---------------------------------------------
    input  wire signed [C4_WIDTH-1:0]  vco2_tuning_word,
    input  wire signed [C4_WIDTH-1:0]  vco2_depth_word,
    input  wire signed [C4_WIDTH-1:0]  vco2_shape_word,
    input  wire signed [31:0]          vco2_partials_word,  // declared shadow
    input  wire [C2_WIDTH-1:0]         vco2_init_phase_word,
    input  wire signed [C3_WIDTH-1:0]  vco2_fq_word,        // declared shadow
    input  wire signed [C1_WIDTH-1:0]  vco2_square_q_word,  // declared shadow
    input  wire signed [C1_WIDTH-1:0]  vco2_left_q_word,    // declared shadow
    output wire signed [C4_WIDTH-1:0]  vco2_m2_word,
    output wire signed [31:0]          vco2_driven_word,
    output wire signed [C1_WIDTH-1:0]  vco2_right_q_word,
    output wire signed [C1_WIDTH-1:0]  vco2_word,
    output wire                        vco2_valid,
    output wire [31:0] vco2_op_mults, vco2_op_adds, vco2_op_narrows,
    output wire [31:0] vco2_op_sats,

    // ---- noise lane (#75) -----------------------------------------------
    input  wire                        noise_en,
    input  wire [C2_WIDTH-1:0]         noise_sound_index,
    input  wire [4:0]                  noise_declared_slot,
    input  wire [7:0]                  noise_byte_in,
    input  wire                        noise_byte_valid,
    input  wire                        noise_in_done,
    output wire signed [C1_WIDTH-1:0]  noise_sample_word,
    output wire                        noise_sample_valid,
    output wire                        noise_error,
    output wire [7:0]                  noise_error_code,
    output wire [19:0]                 noise_bytes_accepted,
    output wire [17:0]                 noise_samples_produced,
    output wire [31:0]                 noise_narrow_count,
    output wire [4:0]                  noise_bound_slot,

    // ---- audio VCAs + pre-normalization mixer (#76) ---------------------
    input  wire signed [C1_WIDTH-1:0]  level_vco_1,
    input  wire signed [C1_WIDTH-1:0]  level_vco_2,
    input  wire signed [C1_WIDTH-1:0]  level_noise,
    output wire signed [C1_WIDTH-1:0]  post_vca_1,
    output wire signed [C1_WIDTH-1:0]  post_vca_2,
    output wire signed [C1_WIDTH-1:0]  post_vca_n,
    output wire signed [C1_WIDTH-1:0]  mix_word,
    output wire        [C1_WIDTH-1:0]  mix_abs,
    output wire        [C1_WIDTH-1:0]  mix_peak_word,
    output wire                        mix_out_valid,
    output wire                        link_valid_out,
    output wire [31:0] mix_op_mults, mix_op_adds, mix_op_narrows, mix_op_sats,
    output wire [31:0] mix_op_rounds, mix_op_peak_cmps, mix_op_acc_faults,

    // ---- normalization replay controller (#77) --------------------------
    input  wire        start,
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
    output wire [C9_WIDTH-1:0] gain_word,
    output wire [31:0] op_compares, op_selects, op_recip_divs, op_mults,
    output wire [31:0] op_narrows, op_saturations
);

    localparam integer N_ADSR   = 6;
    localparam integer N_LFO    = 2;
    localparam integer N_ROUTES = 5;

    // =====================================================================
    // 1. The six envelope engines (#70)
    // =====================================================================
    wire signed [C1_WIDTH-1:0] adsr_env_arr [0:N_ADSR-1];

    genvar ga;
    generate
        for (ga = 0; ga < N_ADSR; ga = ga + 1) begin : ADSR
            adsr_engine #(.INST_ID(ga)) eng (
                .clk                 (clk),
                .rst                 (rst),
                .en                  (ctl_en_adsr),
                .trigger             (ctl_trigger),
                .duration_q          (adsr_duration_q[ga*47 +: 47]),
                .attack_q            (adsr_attack_q[ga*47 +: 47]),
                .decay_q             (adsr_decay_q[ga*47 +: 47]),
                .release_q           (adsr_release_q[ga*47 +: 47]),
                .duration_exact_zero (adsr_duration_zero[ga]),
                .attack_exact_zero   (adsr_attack_zero[ga]),
                .decay_exact_zero    (adsr_decay_zero[ga]),
                .release_exact_zero  (adsr_release_zero[ga]),
                .sustain_entry       (adsr_sustain_entry[ga*C4_WIDTH +: C4_WIDTH]),
                .eps60               (adsr_eps60),
                .shadow_attack       (adsr_shadow_attack[ga*32 +: 32]),
                .shadow_decay        (adsr_shadow_decay[ga*32 +: 32]),
                .shadow_release      (adsr_shadow_release[ga*32 +: 32]),
                .env_word            (adsr_env_arr[ga]),
                .out_valid           (adsr_valid[ga]),
                .op_mults            (adsr_op_mults[ga*32 +: 32]),
                .op_narrows          (adsr_op_narrows[ga*32 +: 32]),
                .op_shadow           (adsr_op_shadow[ga*32 +: 32]),
                .op_rampdivs         (adsr_op_rampdivs[ga*32 +: 32])
            );
            assign adsr_env[ga*C1_WIDTH +: C1_WIDTH] = adsr_env_arr[ga];
        end
    endgenerate

    // =====================================================================
    // 2. The two LFO + control-rate VCA engines (#71)
    //
    // SEAM lfo_rate_env / lfo_gain: which envelope engine plays the rate
    // role and which the VCA-gain role for each side. The frozen model's
    // pairing is (lfo_<n>_rate_adsr -> rate, lfo_<n>_amp_adsr -> gain);
    // index 2/3 are the rate envelopes, 4/5 the amp envelopes.
    // =====================================================================
    wire signed [C1_WIDTH-1:0] lfo_rate_env [0:N_LFO-1];
    wire signed [C1_WIDTH-1:0] lfo_gain     [0:N_LFO-1];
    assign lfo_rate_env[0] = adsr_env_arr[2];
    assign lfo_rate_env[1] = adsr_env_arr[3];
    assign lfo_gain[0]     = adsr_env_arr[4];
    assign lfo_gain[1]     = adsr_env_arr[5];

    wire signed [C1_WIDTH-1:0] lfo_post_arr [0:N_LFO-1];

    genvar gl;
    generate
        for (gl = 0; gl < N_LFO; gl = gl + 1) begin : LFO
            lfo_vca_engine #(.INST_ID(gl)) eng (
                .clk        (clk),
                .rst        (rst),
                .en         (ctl_en_lfo),
                .trigger    (ctl_trigger),
                .freq_word  (lfo_freq[gl*C4_WIDTH +: C4_WIDTH]),
                .depth_word (lfo_depth[gl*C4_WIDTH +: C4_WIDTH]),
                .init_word  (lfo_init[gl*C2_WIDTH +: C2_WIDTH]),
                .w_sin      (lfo_weights[(gl*5 + 0)*32 +: 32]),
                .w_tri      (lfo_weights[(gl*5 + 1)*32 +: 32]),
                .w_saw      (lfo_weights[(gl*5 + 2)*32 +: 32]),
                .w_rsaw     (lfo_weights[(gl*5 + 3)*32 +: 32]),
                .w_sqr      (lfo_weights[(gl*5 + 4)*32 +: 32]),
                .rate_env   (lfo_rate_env[gl]),
                .gain       (lfo_gain[gl]),
                .raw_word   (lfo_raw[gl*C1_WIDTH +: C1_WIDTH]),
                .post_word  (lfo_post_arr[gl]),
                .out_valid  (lfo_valid[gl]),
                .op_mults   (lfo_op_mults[gl*32 +: 32]),
                .op_narrows (lfo_op_narrows[gl*32 +: 32]),
                .op_phases  (lfo_op_phases[gl*32 +: 32]),
                .op_clamps  (lfo_op_clamps[gl*32 +: 32])
            );
            assign lfo_post[gl*C1_WIDTH +: C1_WIDTH] = lfo_post_arr[gl];
        end
    endgenerate

    // =====================================================================
    // 3. The modulation matrix (#72)
    //
    // SEAM col_adsr_1 / col_adsr_2 / col_lfo_1 / col_lfo_2: the pinned
    // source order (adsr_1, adsr_2, lfo_1, lfo_2) the twenty depth words
    // are packed against. A permutation here is the genuine RTL
    // parameter-shuffle fault.
    // =====================================================================
    wire signed [C1_WIDTH-1:0] col_adsr_1 = adsr_env_arr[0];
    wire signed [C1_WIDTH-1:0] col_adsr_2 = adsr_env_arr[1];
    wire signed [C1_WIDTH-1:0] col_lfo_1  = lfo_post_arr[0];
    wire signed [C1_WIDTH-1:0] col_lfo_2  = lfo_post_arr[1];

    wire signed [C1_WIDTH-1:0] mm_out_arr [0:N_ROUTES-1];

    mod_matrix_engine matrix (
        .clk        (clk),
        .rst        (rst),
        .en         (ctl_en_mm),
        .trigger    (ctl_trigger),
        .depths_r0  (depths_r0),
        .depths_r1  (depths_r1),
        .depths_r2  (depths_r2),
        .depths_r3  (depths_r3),
        .depths_r4  (depths_r4),
        .col_adsr_1 (col_adsr_1),
        .col_adsr_2 (col_adsr_2),
        .col_lfo_1  (col_lfo_1),
        .col_lfo_2  (col_lfo_2),
        .out_r0     (mm_out_arr[0]),
        .out_r1     (mm_out_arr[1]),
        .out_r2     (mm_out_arr[2]),
        .out_r3     (mm_out_arr[3]),
        .out_r4     (mm_out_arr[4]),
        .out_valid  (mm_valid),
        .op_mults   (mm_op_mults),
        .op_adds    (mm_op_adds),
        .op_narrows (mm_op_narrows),
        .op_sats    (mm_op_sats)
    );

    genvar gm;
    generate
        for (gm = 0; gm < N_ROUTES; gm = gm + 1) begin : MMOUT
            assign mm_out[gm*C1_WIDTH +: C1_WIDTH] = mm_out_arr[gm];
        end
    endgenerate

    // =====================================================================
    // 4. The control/audio-rate crossing: five endpoint-aligned upsample
    //    engines (#72). The matrix's registered route words are written
    //    into the column memories during p3 of each control tick -- this is
    //    the ONLY control-to-audio crossing in the profile (DR-0010), and
    //    the column memory is not a clip buffer: it holds 1,764 control
    //    words per route, not 176,400 samples.
    // =====================================================================
    wire up_en_eff = up_en | ctl_load | audio_go;

    wire signed [C1_WIDTH-1:0] up_arr [0:N_ROUTES-1];

    genvar gu;
    generate
        for (gu = 0; gu < N_ROUTES; gu = gu + 1) begin : UP
            upsample_engine #(.ROUTE_ID(gu)) eng (
                .clk            (clk),
                .rst            (rst),
                .en             (up_en_eff),
                .trigger        (audio_trigger),
                .load           (ctl_load),
                .load_index     (ctl_load_index),
                .column_word    (mm_out_arr[gu]),
                .go             (audio_go),
                .audio_word     (up_arr[gu]),
                .audio_valid    (up_valid[gu]),
                .out_index      (),
                .op_mults       (up_op_mults[gu*32 +: 32]),
                .op_adds        (up_op_adds[gu*32 +: 32]),
                .op_narrows     (up_op_narrows[gu*32 +: 32]),
                .op_frac_words  (up_op_frac_words[gu*32 +: 32]),
                .op_coord_steps (up_op_coord_steps[gu*32 +: 32]),
                .op_sats        (up_op_sats[gu*32 +: 32]),
                .out_count      (up_out_count[gu*32 +: 32])
            );
            assign up_word[gu*C1_WIDTH +: C1_WIDTH] = up_arr[gu];
        end
    endgenerate

    // SEAM up_pitch_vco_1 / up_pitch_vco_2 / amp_*: the route order
    // (vco_1_pitch, vco_1_amp, vco_2_pitch, vco_2_amp, noise_amp). Swapping
    // an amplitude column here is the genuine RTL route/gain fault.
    wire signed [C1_WIDTH-1:0] up_pitch_vco_1 = up_arr[0];
    wire signed [C1_WIDTH-1:0] amp_vco_1      = up_arr[1];
    wire signed [C1_WIDTH-1:0] up_pitch_vco_2 = up_arr[2];
    wire signed [C1_WIDTH-1:0] amp_vco_2      = up_arr[3];
    wire signed [C1_WIDTH-1:0] amp_noise      = up_arr[4];

    // =====================================================================
    // 5. The two audio sources with resident RTL phase state (#73, #74)
    // =====================================================================
    sine_vco_engine vco_1 (
        .clk                (clk),
        .rst                (rst),
        .en                 (vco_en),
        .trigger            (audio_trigger),
        .midi_f0_word       (midi_f0_word),
        .tuning_word        (vco1_tuning_word),
        .depth_word         (vco1_depth_word),
        .initial_phase_word (vco1_init_phase_word),
        .up_pitch           (up_pitch_vco_1),
        .fq_word            (vco1_fq_word),
        .vco_word           (vco1_word),
        .phase_out          (vco1_phase_out),
        .out_valid          (vco1_valid),
        .op_mults           (vco1_op_mults),
        .op_adds            (vco1_op_adds),
        .op_narrows         (vco1_op_narrows),
        .op_shadows         (vco1_op_shadows),
        .op_sats            (vco1_op_sats),
        .op_clamps          (vco1_op_clamps)
    );

    square_saw_vco_engine vco_2 (
        .clk             (clk),
        .rst             (rst),
        .en              (vco_en),
        .trigger         (audio_trigger),
        .midi_f0_word    (midi_f0_word),
        .tuning_word     (vco2_tuning_word),
        .depth_word      (vco2_depth_word),
        .shape_word      (vco2_shape_word),
        .partials_word   (vco2_partials_word),
        .init_phase_word (vco2_init_phase_word),
        .up_pitch_word   (up_pitch_vco_2),
        .fq_word         (vco2_fq_word),
        .square_q_word   (vco2_square_q_word),
        .left_q_word     (vco2_left_q_word),
        .m2_word         (vco2_m2_word),
        .driven_word     (vco2_driven_word),
        .right_q_word    (vco2_right_q_word),
        .v2_word         (vco2_word),
        .out_valid       (vco2_valid),
        .op_mults        (vco2_op_mults),
        .op_adds         (vco2_op_adds),
        .op_narrows      (vco2_op_narrows),
        .op_sats         (vco2_op_sats)
    );

    // =====================================================================
    // 6. The host-fed exact noise lane (#75). Its byte port is 8 bits, so
    //    one audio sample costs four byte cycles -- that, and nothing else,
    //    is why the audio group below is four cycles wide.
    // =====================================================================
    noise_stream_dut noise (
        .clk              (clk),
        .rst              (rst),
        .en               (noise_en),
        .sound_index      (noise_sound_index),
        .declared_slot    (noise_declared_slot),
        .byte_in          (noise_byte_in),
        .byte_valid       (noise_byte_valid),
        .in_done          (noise_in_done),
        .sample_word      (noise_sample_word),
        .sample_valid     (noise_sample_valid),
        .error            (noise_error),
        .error_code       (noise_error_code),
        .bytes_accepted   (noise_bytes_accepted),
        .samples_produced (noise_samples_produced),
        .narrow_count     (noise_narrow_count),
        .bound_slot       (noise_bound_slot)
    );

    // SEAM raw_noise_bound: the noise lane's registered sample into the
    // mixer's noise input. A rotation/hold here is the genuine RTL
    // wrong-noise fault.
    wire signed [C1_WIDTH-1:0] raw_noise_bound = noise_sample_word;

    // =====================================================================
    // 7. The three audio VCAs + pre-normalization mixer (#76)
    // =====================================================================
    audio_mix_engine mixer (
        .clk           (clk),
        .rst           (rst),
        .en            (mix_en),
        .trigger       (audio_trigger),
        .level_vco_1   (level_vco_1),
        .level_vco_2   (level_vco_2),
        .level_noise   (level_noise),
        .raw_vco_1     (vco1_word),
        .raw_vco_2     (vco2_word),
        .raw_noise     (raw_noise_bound),
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

    // =====================================================================
    // 8. The normalization replay controller + two-pass sequencer (#77)
    // =====================================================================
    // SEAM link_valid: the mixer -> replay sample stream. A mutant that
    // drops, duplicates or re-times a sample here is the missing-sample
    // regression the flow must localize.
    wire link_valid = mix_out_valid;
    assign link_valid_out = link_valid;

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
