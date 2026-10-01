// File-driven testbench over the integrated mixer -> replay-controller top
// (issue #79, tb/sv/one_shot_tail_top.sv).
//
// Run protocol (paths cwd-relative, controlled by tb/run_oneshot.py):
// - runs.txt: one integer R, the number of back-to-back clip renders (no
//   reset between them; each begins with `start` from the prior `done`).
// - Per run r:
//   - run<r>_params.txt: three C1 Q2.21 level entry words, unsigned 32-bit
//     bit patterns ("level_vco_1 level_vco_2 level_noise").
//   - run<r>_streams.txt: 176,400 lines "raw1 raw2 rawn amp1 amp2 ampn".
//   - run<r>_mixcap.txt: one line per link-valid cycle "cycle pass word"
//     (pass is the replay engine's own pass_index at that edge). Sampled on
//     link_valid, not mix_out_valid, so it reflects what the replay engine
//     actually consumed -- a link-side drop/duplicate/re-time mutation is
//     visible here, localized to the cycle it happens on.
//   - run<r>_outcap.txt: one line per released output "cycle word".
//   - run<r>_status.txt: one line "error error_code peak gain branch done
//     pass compares selects recip_divs mults narrows saturations mix_peak".
//   - run<r>_ops.txt: two lines "P1|P2 mults adds narrows sats rounds
//     peak_cmps acc_faults", the mixer counters at the end of each pass.
// Each pass is a fresh mixer trigger over the identical streams (DR-0010 P4
// re-render, no clip buffer). PDK-free Icarus Verilog.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer N        = SCHED_SAMPLES_PER_PASS;
    localparam integer MAX_RUNS = 8;

    reg clk = 1'b0;
    reg rst = 1'b1;
    reg en  = 1'b0;
    reg mix_trig = 1'b0;
    reg start = 1'b0;
    reg mix_done = 1'b0;
    always #5 clk = ~clk;

    reg signed [C1_WIDTH-1:0] level_vco_1, level_vco_2, level_noise;
    reg signed [C1_WIDTH-1:0] raw_vco_1, raw_vco_2, raw_noise;
    reg signed [C1_WIDTH-1:0] amp_vco_1, amp_vco_2, amp_noise;

    wire signed [C1_WIDTH-1:0] post_vca_1, post_vca_2, post_vca_n, mix_word;
    wire        [C1_WIDTH-1:0] mix_abs, mix_peak_word;
    wire                       mix_out_valid;
    wire                       link_valid;
    wire [31:0] m_m, m_a, m_n, m_s, m_r, m_p, m_f;
    wire signed [C1_WIDTH-1:0] audio_out;
    wire        audio_out_valid, busy, done, error, branch_normalized;
    wire [7:0]  error_code;
    wire [1:0]  pass_index;
    wire [17:0] samples_this_pass;
    wire [C1_WIDTH-1:0] peak_word;
    wire [C9_WIDTH-1:0] gain_word;
    wire [31:0] op_compares, op_selects, op_recip_divs, op_mults, op_narrows,
                op_saturations;

    one_shot_tail_top dut (
        .clk(clk), .rst(rst), .en(en), .mix_trigger(mix_trig),
        .level_vco_1(level_vco_1), .level_vco_2(level_vco_2),
        .level_noise(level_noise),
        .raw_vco_1(raw_vco_1), .raw_vco_2(raw_vco_2), .raw_noise(raw_noise),
        .amp_vco_1(amp_vco_1), .amp_vco_2(amp_vco_2), .amp_noise(amp_noise),
        .post_vca_1(post_vca_1), .post_vca_2(post_vca_2),
        .post_vca_n(post_vca_n), .mix_word(mix_word), .mix_abs(mix_abs),
        .mix_peak_word(mix_peak_word), .mix_out_valid(mix_out_valid),
        .mix_op_mults(m_m), .mix_op_adds(m_a), .mix_op_narrows(m_n),
        .mix_op_sats(m_s), .mix_op_rounds(m_r), .mix_op_peak_cmps(m_p),
        .mix_op_acc_faults(m_f),
        .link_valid(link_valid),
        .start(start), .mix_done(mix_done),
        .audio_out(audio_out), .audio_out_valid(audio_out_valid),
        .busy(busy), .done(done), .error(error), .error_code(error_code),
        .pass_index(pass_index), .samples_this_pass(samples_this_pass),
        .branch_normalized(branch_normalized), .peak_word(peak_word),
        .gain_word(gain_word), .op_compares(op_compares),
        .op_selects(op_selects), .op_recip_divs(op_recip_divs),
        .op_mults(op_mults), .op_narrows(op_narrows),
        .op_saturations(op_saturations)
    );

    reg signed [C1_WIDTH-1:0] s_raw1 [0:N-1];
    reg signed [C1_WIDTH-1:0] s_raw2 [0:N-1];
    reg signed [C1_WIDTH-1:0] s_rawn [0:N-1];
    reg signed [C1_WIDTH-1:0] s_amp1 [0:N-1];
    reg signed [C1_WIDTH-1:0] s_amp2 [0:N-1];
    reg signed [C1_WIDTH-1:0] s_ampn [0:N-1];

    integer mixcap_fd [0:MAX_RUNS-1];
    integer outcap_fd [0:MAX_RUNS-1];
    integer RUNS, r, fd, code, k;
    integer v0, v1, v2, v3, v4, v5;
    integer cyc;
    reg [1023:0] fname;

    initial cyc = 0;
    always @(posedge clk) begin
        cyc <= cyc + 1;
        if (!rst && r >= 0) begin
            if (link_valid)
                $fwrite(mixcap_fd[r], "%0d %0d %0d\n", cyc, pass_index, mix_word);
            if (audio_out_valid)
                $fwrite(outcap_fd[r], "%0d %0d\n", cyc, audio_out);
        end
    end

    task load_run(input integer run);
        begin
            $sformat(fname, "run%0d_params.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin $display("TB-ERROR cannot open %0s", fname); $finish; end
            code = $fscanf(fd, "%d %d %d", v0, v1, v2);
            $fclose(fd);
            if (code != 3) begin $display("TB-ERROR short params"); $finish; end
            level_vco_1 = v0[C1_WIDTH-1:0];
            level_vco_2 = v1[C1_WIDTH-1:0];
            level_noise = v2[C1_WIDTH-1:0];
            $sformat(fname, "run%0d_streams.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin $display("TB-ERROR cannot open %0s", fname); $finish; end
            for (k = 0; k < N; k = k + 1) begin
                code = $fscanf(fd, "%d %d %d %d %d %d", v0, v1, v2, v3, v4, v5);
                if (code != 6) begin
                    $display("TB-ERROR short streams line %0d", k);
                    $finish;
                end
                s_raw1[k] = v0[C1_WIDTH-1:0];
                s_raw2[k] = v1[C1_WIDTH-1:0];
                s_rawn[k] = v2[C1_WIDTH-1:0];
                s_amp1[k] = v3[C1_WIDTH-1:0];
                s_amp2[k] = v4[C1_WIDTH-1:0];
                s_ampn[k] = v5[C1_WIDTH-1:0];
            end
            $fclose(fd);
        end
    endtask

    // One pass: a fresh mixer trigger, N fed samples, a drain cycle for the
    // mixer's registered last word, then the host's mix_done pulse.
    task walk_pass(input integer run, input integer pass);
        begin
            @(negedge clk);
            en = 1'b1;
            mix_trig = 1'b1;
            if (pass == 1) start = 1'b1;
            @(negedge clk);
            mix_trig = 1'b0;
            start = 1'b0;
            for (k = 0; k < N; k = k + 1) begin
                raw_vco_1 = s_raw1[k]; raw_vco_2 = s_raw2[k]; raw_noise = s_rawn[k];
                amp_vco_1 = s_amp1[k]; amp_vco_2 = s_amp2[k]; amp_noise = s_ampn[k];
                @(negedge clk);
            end
            en = 1'b0;
            @(negedge clk);   // the replay engine consumes the last word
            mix_done = 1'b1;
            @(negedge clk);
            mix_done = 1'b0;
            @(negedge clk);
            $sformat(fname, "run%0d_ops.txt", run);
            fd = $fopen(fname, (pass == 1) ? "w" : "a");
            $fwrite(fd, "P%0d %0d %0d %0d %0d %0d %0d %0d\n", pass,
                m_m, m_a, m_n, m_s, m_r, m_p, m_f);
            $fclose(fd);
        end
    endtask

    initial begin
        fd = $fopen("runs.txt", "r");
        if (fd == 0) begin $display("TB-ERROR cannot open runs.txt"); $finish; end
        code = $fscanf(fd, "%d", RUNS);
        $fclose(fd);
        if (code != 1 || RUNS < 1 || RUNS > MAX_RUNS) begin
            $display("TB-ERROR bad run count (%0d)", RUNS);
            $finish;
        end
        r = -1;
        for (k = 0; k < RUNS; k = k + 1) begin
            $sformat(fname, "run%0d_mixcap.txt", k);
            mixcap_fd[k] = $fopen(fname, "w");
            $sformat(fname, "run%0d_outcap.txt", k);
            outcap_fd[k] = $fopen(fname, "w");
        end
        raw_vco_1 = 0; raw_vco_2 = 0; raw_noise = 0;
        amp_vco_1 = 0; amp_vco_2 = 0; amp_noise = 0;
        level_vco_1 = 0; level_vco_2 = 0; level_noise = 0;
        @(negedge clk);
        @(negedge clk);
        rst = 1'b0;
        @(negedge clk);
        for (r = 0; r < RUNS; r = r + 1) begin
            load_run(r);
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
            $fclose(mixcap_fd[k]);
            $fclose(outcap_fd[k]);
        end
        $display("TB-DONE %0d", RUNS);
        $finish;
    end

endmodule
