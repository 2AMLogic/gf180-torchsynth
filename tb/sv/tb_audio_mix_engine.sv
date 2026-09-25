// File-driven testbench over the audio VCA + mixer engine (issue #76).
//
// Run protocol (paths cwd-relative, controlled by tb/run_tb.py):
// - runs.txt: one integer, the number of back-to-back runs R.
// - Per run r in 0..R-1:
//   - run<r>_params.txt: one line, three signed C1 Q2.21 mixer level
//     entry words written as unsigned 32-bit bit patterns (read with %d
//     and sliced, like the #70/#71/#72/#73 words): level_vco_1,
//     level_vco_2, level_noise.
//   - run<r>_streams.txt: one line per audio sample,
//     "raw1 raw2 rawn amp1 amp2 ampn": the three C1 source words
//     (vco_1.raw / vco_2.raw / noise.raw from the #73 / #74 / #75 lanes)
//     and the three C1 endpoint-aligned amplitude columns
//     (control_upsample.*_amp from the #72 lane).
//   - run<r>_captured.txt: one line per emitted sample,
//     "pv1 pv2 pvn mix abs peak": the three post-VCA words, the
//     pre-normalization mix word, its magnitude, and the running peak.
//   - run<r>_cycles.txt: one integer, the enabled walk cycles (the
//     complete-clip schedule measurement).
//   - run<r>_ops.txt: one line
//     "M mults adds narrows sats rounds peak_cmps acc_faults".
//
// The +max_n=<int> plusarg caps the audio walk -- a mutation
// demonstration knob only; committed-case runs walk the full 176,400
// samples. Every run triggers fresh: no cross-run state (level words,
// peak register, counters) may survive a trigger. PDK-free Icarus
// Verilog, no vendor or PDK cells.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer AUDIO_SAMPLES = 176400;
    localparam integer MAX_RUNS      = 8;

    reg clk = 1'b0;
    reg rst = 1'b1;
    reg en  = 1'b0;

    always #5 clk = ~clk;

    reg mix_trig;
    reg signed [C1_WIDTH-1:0] level_vco_1, level_vco_2, level_noise;
    reg signed [C1_WIDTH-1:0] raw_vco_1, raw_vco_2, raw_noise;
    reg signed [C1_WIDTH-1:0] amp_vco_1, amp_vco_2, amp_noise;

    wire signed [C1_WIDTH-1:0] post_vca_1, post_vca_2, post_vca_n, mix_word;
    wire        [C1_WIDTH-1:0] mix_abs, peak_word;
    wire                       mix_valid;
    wire [31:0] op_m, op_a, op_n, op_s, op_r, op_p, op_f;

    integer max_n;

    audio_mix_engine eng (
        .clk           (clk),
        .rst           (rst),
        .en            (en),
        .trigger       (mix_trig),
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
        .peak_word     (peak_word),
        .out_valid     (mix_valid),
        .op_mults      (op_m),
        .op_adds       (op_a),
        .op_narrows    (op_n),
        .op_sats       (op_s),
        .op_rounds     (op_r),
        .op_peak_cmps  (op_p),
        .op_acc_faults (op_f)
    );

    integer cap_fd [0:MAX_RUNS-1];
    integer RUNS;
    integer r;
    integer fd;
    integer code;
    integer v0, v1, v2, v3, v4, v5;
    integer walked;
    reg [1023:0] fname;

    task run_one(input integer run);
        begin
            $sformat(fname, "run%0d_params.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end
            code = $fscanf(fd, "%d %d %d", v0, v1, v2);
            if (code != 3) begin
                $display("TB-ERROR short params line (code %0d)", code);
                $finish;
            end
            $fclose(fd);
            level_vco_1 = v0[C1_WIDTH-1:0];
            level_vco_2 = v1[C1_WIDTH-1:0];
            level_noise = v2[C1_WIDTH-1:0];

            $sformat(fname, "run%0d_streams.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end

            // ---- the audio walk ------------------------------------
            @(negedge clk);
            rst = 1'b0;
            en  = 1'b1;
            mix_trig = 1'b1;
            @(posedge clk);
            @(negedge clk);
            mix_trig = 1'b0;
            walked = 0;
            while (walked < max_n) begin
                code = $fscanf(fd, "%d %d %d %d %d %d",
                    v0, v1, v2, v3, v4, v5);
                if (code != 6) begin
                    $display("TB-ERROR short streams line (sample %0d, code %0d)",
                        walked, code);
                    $finish;
                end
                raw_vco_1 = v0[C1_WIDTH-1:0];
                raw_vco_2 = v1[C1_WIDTH-1:0];
                raw_noise = v2[C1_WIDTH-1:0];
                amp_vco_1 = v3[C1_WIDTH-1:0];
                amp_vco_2 = v4[C1_WIDTH-1:0];
                amp_noise = v5[C1_WIDTH-1:0];
                @(posedge clk);
                #1;
                if (mix_valid) begin
                    $fwrite(cap_fd[run], "%0d %0d %0d %0d %0d %0d\n",
                        post_vca_1, post_vca_2, post_vca_n, mix_word,
                        mix_abs, peak_word);
                end
                walked = walked + 1;
                @(negedge clk);
            end
            $fclose(fd);
            en = 1'b0;

            $sformat(fname, "run%0d_cycles.txt", run);
            fd = $fopen(fname, "w");
            $fwrite(fd, "%0d\n", walked);
            $fclose(fd);
            $sformat(fname, "run%0d_ops.txt", run);
            fd = $fopen(fname, "w");
            $fwrite(fd, "M %0d %0d %0d %0d %0d %0d %0d\n",
                op_m, op_a, op_n, op_s, op_r, op_p, op_f);
            $fclose(fd);
        end
    endtask

    initial begin
        if (!$value$plusargs("max_n=%d", max_n)) begin
            max_n = AUDIO_SAMPLES;
        end
        fd = $fopen("runs.txt", "r");
        if (fd == 0) begin
            $display("TB-ERROR cannot open runs.txt");
            $finish;
        end
        code = $fscanf(fd, "%d", RUNS);
        $fclose(fd);
        if (code != 1 || RUNS < 1 || RUNS > MAX_RUNS) begin
            $display("TB-ERROR bad run count (%0d)", RUNS);
            $finish;
        end

        for (r = 0; r < RUNS; r = r + 1) begin
            $sformat(fname, "run%0d_captured.txt", r);
            cap_fd[r] = $fopen(fname, "w");
            if (cap_fd[r] == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end
        end

        for (r = 0; r < RUNS; r = r + 1)
            run_one(r);

        for (r = 0; r < RUNS; r = r + 1)
            $fclose(cap_fd[r]);

        $display("TB-DONE %0d", RUNS);
        $finish;
    end

endmodule
