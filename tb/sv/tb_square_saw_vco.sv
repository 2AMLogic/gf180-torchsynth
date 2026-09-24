// File-driven testbench over the square/saw VCO engine (issue #74).
//
// Run protocol (paths cwd-relative, controlled by tb/run_tb.py):
// - runs.txt: one integer, the number of back-to-back runs R.
// - Per run r in 0..R-1:
//   - run<r>_statics.txt: one line, seven integers -- the C4 Q10.21
//     midi_f0/tuning/depth/shape words, the s14.17 partials-constant
//     shadow word, the C2 initial-turn word (both host shadow replays),
//     and the sample count. Words are written as unsigned 32-bit bit
//     patterns (read with %d and sliced, like the #70/#71/#72 words).
//   - run<r>_stream.txt: sample-count lines of four unsigned 32-bit bit
//     patterns: the Q2.21 pitch column word, the Q16.15 exp2-shadow
//     frequency word, and the two Q2.21 tanh-shadow fanout words
//     (square_q, left_q), per sample.
//   - run<r>_out.txt: sample-count lines of four signed words: the clamped
//     C4 pitch word m2, the s14.17 driven word, the C1 right word, and the
//     C1 vco_2.raw output word.
//   - run<r>_ops.txt: "M mults adds narrows sats" and cycles.txt carries
//     the enabled cycle count for the runner's measured-vs-budget headroom
//     report (a partial-DUT measurement: one sample per cycle, not the
//     DR-0010 serialized schedule).
//
// Every run triggers fresh: no cross-run state may survive a trigger
// (static words, phase, counters). PDK-free Icarus Verilog.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer MAX_SAMPLES = 176400;
    localparam integer MAX_RUNS    = 16;

    reg clk = 1'b0;
    reg rst = 1'b1;
    reg en  = 1'b0;

    always #5 clk = ~clk;

    reg        trig;
    reg signed [C4_WIDTH-1:0] midi_f0_w, tuning_w, depth_w, shape_w;
    reg signed [31:0]         partials_w;
    reg [C2_WIDTH-1:0]        init_phase_w;
    reg signed [C1_WIDTH-1:0] up_pitch_w, square_q_w, left_q_w;
    reg signed [C3_WIDTH-1:0] fq_w;

    wire signed [C4_WIDTH-1:0] m2_w;
    wire signed [31:0]         driven_w;
    wire signed [C1_WIDTH-1:0] right_w;
    wire signed [C1_WIDTH-1:0] v2_w;
    wire                       out_valid;
    wire [31:0] op_m, op_a, op_n, op_s;

    square_saw_vco_engine dut (
        .clk            (clk),
        .rst            (rst),
        .en             (en),
        .trigger        (trig),
        .midi_f0_word   (midi_f0_w),
        .tuning_word    (tuning_w),
        .depth_word     (depth_w),
        .shape_word     (shape_w),
        .partials_word  (partials_w),
        .init_phase_word(init_phase_w),
        .up_pitch_word  (up_pitch_w),
        .fq_word        (fq_w),
        .square_q_word  (square_q_w),
        .left_q_word    (left_q_w),
        .m2_word        (m2_w),
        .driven_word    (driven_w),
        .right_q_word   (right_w),
        .v2_word        (v2_w),
        .out_valid      (out_valid),
        .op_mults       (op_m),
        .op_adds        (op_a),
        .op_narrows     (op_n),
        .op_sats        (op_s)
    );

    integer fd, ofd, cfd;
    integer code, RUNS, r, i, n_samples;
    integer v_midi, v_tun, v_dep, v_shp, v_part, v_init, v_n;
    integer v_up, v_fq, v_sq, v_left;
    integer measured_cycles;
    reg [1023:0] fname;

    task run_one(input integer run);
        begin
            $sformat(fname, "run%0d_statics.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end
            code = $fscanf(fd, "%d %d %d %d %d %d %d",
                v_midi, v_tun, v_dep, v_shp, v_part, v_init, v_n);
            $fclose(fd);
            if (code != 7) begin
                $display("TB-ERROR short statics line (code %0d)", code);
                $finish;
            end
            n_samples = v_n;
            if (n_samples <= 0 || n_samples > MAX_SAMPLES) begin
                $display("TB-ERROR sample count %0d outside 1..%0d",
                    n_samples, MAX_SAMPLES);
                $finish;
            end
            midi_f0_w    = v_midi[C4_WIDTH-1:0];
            tuning_w     = v_tun[C4_WIDTH-1:0];
            depth_w      = v_dep[C4_WIDTH-1:0];
            shape_w      = v_shp[C4_WIDTH-1:0];
            partials_w   = v_part[31:0];
            init_phase_w = v_init[C2_WIDTH-1:0];

            $sformat(fname, "run%0d_stream.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end

            // Trigger (statics stable), then stream one row per sample.
            @(negedge clk);
            rst = 1'b0;
            en  = 1'b1;
            trig = 1'b1;
            @(posedge clk);
            @(negedge clk);
            trig = 1'b0;

            $sformat(fname, "run%0d_out.txt", run);
            ofd = $fopen(fname, "w");
            if (ofd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end
            measured_cycles = 0;
            for (i = 0; i < n_samples; i = i + 1) begin
                code = $fscanf(fd, "%d %d %d %d", v_up, v_fq, v_sq, v_left);
                if (code != 4) begin
                    $display("TB-ERROR short stream row %0d (code %0d)",
                        i, code);
                    $finish;
                end
                up_pitch_w = v_up[C1_WIDTH-1:0];
                fq_w       = v_fq[C3_WIDTH-1:0];
                square_q_w = v_sq[C1_WIDTH-1:0];
                left_q_w   = v_left[C1_WIDTH-1:0];
                @(posedge clk);
                measured_cycles = measured_cycles + 1;
                #1;
                if (out_valid) begin
                    $fwrite(ofd, "%0d %0d %0d %0d\n",
                        $signed(m2_w), $signed(driven_w),
                        $signed(right_w), $signed(v2_w));
                end
                @(negedge clk);
            end
            $fclose(fd);
            $fclose(ofd);

            $sformat(fname, "run%0d_ops.txt", run);
            fd = $fopen(fname, "w");
            $fwrite(fd, "M %0d %0d %0d %0d\n", op_m, op_a, op_n, op_s);
            $fclose(fd);
            $sformat(fname, "run%0d_cycles.txt", run);
            cfd = $fopen(fname, "w");
            $fwrite(cfd, "%0d\n", measured_cycles);
            $fclose(cfd);
        end
    endtask

    initial begin
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

        for (r = 0; r < RUNS; r = r + 1)
            run_one(r);

        $display("TB-DONE %0d", RUNS);
        $finish;
    end

endmodule
