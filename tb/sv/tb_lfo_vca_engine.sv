// File-driven testbench over the two-instance LFO + control-VCA engine
// (issue #71).
//
// Run protocol (paths cwd-relative, controlled by tb/run_tb.py):
// - runs.txt: one integer, the number of back-to-back runs R.
// - Per run r in 0..R-1:
//   - run<r>_params.txt: two lines, one per instance (lfo_1, lfo_2),
//       eight integers:
//       freq depth init w_sin w_tri w_saw w_rsaw w_sqr
//     freq/depth are the signed C4 Q10.21 entry words; init is the
//     host-formed S3 initial-turns u32 word written as an unsigned
//     decimal (bit-pattern read, like the #70 length-word halves);
//     w_* are the five shadow-replayed normalized Q2.30 weight words in
//     model order (sin, tri, saw, rsaw, sqr).
//   - run<r>_streams<i>.txt: CONTROL_SAMPLES lines of two signed Q2.21
//     control words: the rate-envelope (rate-ADSR output) and the VCA
//     gain (amp-ADSR output), the #70 engines' declared outputs.
//   - run<r>_captured<i>.txt: CONTROL_SAMPLES lines of two signed C1
//     Q2.21 words: the LFO raw word and the post-control-VCA word.
//   - run<r>_ops.txt: two lines "mults narrows phases clamps".
//
// Both instances run concurrently with fully independent phase/state;
// every run triggers fresh (no cross-run or cross-instance state may
// survive a trigger). PDK-free Icarus Verilog, no vendor or PDK cells.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer N_INST = 2;
    localparam integer CONTROL_SAMPLES = 1764;
    localparam integer MAX_RUNS = 8;

    reg clk = 1'b0;
    reg rst = 1'b1;
    reg en = 1'b0;

    always #5 clk = ~clk;

    reg                    trig  [0:N_INST-1];
    reg signed [C4_WIDTH-1:0] freq_w [0:N_INST-1];
    reg signed [C4_WIDTH-1:0] depth_w[0:N_INST-1];
    reg [C2_WIDTH-1:0]     init_w [0:N_INST-1];
    reg signed [31:0]      ws     [0:N_INST-1][0:4];
    reg signed [C1_WIDTH-1:0] rate_env [0:N_INST-1];
    reg signed [C1_WIDTH-1:0] gain     [0:N_INST-1];

    wire signed [23:0] raw_w  [0:N_INST-1];
    wire signed [23:0] post_w [0:N_INST-1];
    wire               ov     [0:N_INST-1];
    wire [31:0]        op_m   [0:N_INST-1];
    wire [31:0]        op_n   [0:N_INST-1];
    wire [31:0]        op_p   [0:N_INST-1];
    wire [31:0]        op_c   [0:N_INST-1];

    genvar gi;
    generate
        for (gi = 0; gi < N_INST; gi = gi + 1) begin : ENG
            lfo_vca_engine #(.INST_ID(gi)) eng (
                .clk       (clk),
                .rst       (rst),
                .en        (en),
                .trigger   (trig[gi]),
                .freq_word (freq_w[gi]),
                .depth_word(depth_w[gi]),
                .init_word (init_w[gi]),
                .w_sin     (ws[gi][0]),
                .w_tri     (ws[gi][1]),
                .w_saw     (ws[gi][2]),
                .w_rsaw    (ws[gi][3]),
                .w_sqr     (ws[gi][4]),
                .rate_env  (rate_env[gi]),
                .gain      (gain[gi]),
                .raw_word  (raw_w[gi]),
                .post_word (post_w[gi]),
                .out_valid (ov[gi]),
                .op_mults  (op_m[gi]),
                .op_narrows(op_n[gi]),
                .op_phases (op_p[gi]),
                .op_clamps (op_c[gi])
            );
        end
    endgenerate

    // Stream memories for the current run: instance-major flat arrays.
    reg signed [C1_WIDTH-1:0] mem_rate [0:N_INST*CONTROL_SAMPLES-1];
    reg signed [C1_WIDTH-1:0] mem_gain [0:N_INST*CONTROL_SAMPLES-1];

    // One output stream per instance per run, opened up front.
    integer cap [0:MAX_RUNS*N_INST-1];

    integer RUNS;
    integer r;
    integer i;
    integer t;
    integer fd;
    integer code;
    integer vf, vd, vi, v0, v1, v2, v3, v4;
    integer va, vg, vr, vp;
    reg [1023:0] fname;

    task load_run(input integer run);
        integer i2;
        integer t2;
        begin
            $sformat(fname, "run%0d_params.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end
            for (i2 = 0; i2 < N_INST; i2 = i2 + 1) begin
                code = $fscanf(fd, "%d %d %d %d %d %d %d %d",
                    vf, vd, vi, v0, v1, v2, v3, v4);
                if (code != 8) begin
                    $display("TB-ERROR short params line (inst %0d, code %0d)", i2, code);
                    $finish;
                end
                freq_w[i2]  = vf[C4_WIDTH-1:0];
                depth_w[i2] = vd[C4_WIDTH-1:0];
                init_w[i2]  = vi[C2_WIDTH-1:0];
                ws[i2][0]   = v0[31:0];
                ws[i2][1]   = v1[31:0];
                ws[i2][2]   = v2[31:0];
                ws[i2][3]   = v3[31:0];
                ws[i2][4]   = v4[31:0];
            end
            $fclose(fd);
            for (i2 = 0; i2 < N_INST; i2 = i2 + 1) begin
                $sformat(fname, "run%0d_streams%0d.txt", run, i2);
                fd = $fopen(fname, "r");
                if (fd == 0) begin
                    $display("TB-ERROR cannot open %0s", fname);
                    $finish;
                end
                for (t2 = 0; t2 < CONTROL_SAMPLES; t2 = t2 + 1) begin
                    code = $fscanf(fd, "%d %d", va, vg);
                    if (code != 2) begin
                        $display("TB-ERROR short stream row (inst %0d tick %0d)", i2, t2);
                        $finish;
                    end
                    mem_rate[i2*CONTROL_SAMPLES + t2] = va[C1_WIDTH-1:0];
                    mem_gain[i2*CONTROL_SAMPLES + t2] = vg[C1_WIDTH-1:0];
                end
                $fclose(fd);
            end
        end
    endtask

    task run_one(input integer run);
        integer i3;
        integer t3;
        begin
            load_run(run);

            // Trigger both instances (one en cycle), then stream ticks.
            @(negedge clk);
            rst = 1'b0;
            en = 1'b1;
            for (i3 = 0; i3 < N_INST; i3 = i3 + 1)
                trig[i3] = 1'b1;
            @(posedge clk);
            @(negedge clk);
            for (i3 = 0; i3 < N_INST; i3 = i3 + 1)
                trig[i3] = 1'b0;

            for (t3 = 0; t3 < CONTROL_SAMPLES; t3 = t3 + 1) begin
                // Envelope streams for the tick the engines hold in `index`.
                for (i3 = 0; i3 < N_INST; i3 = i3 + 1) begin
                    rate_env[i3] = mem_rate[i3*CONTROL_SAMPLES + t3];
                    gain[i3]     = mem_gain[i3*CONTROL_SAMPLES + t3];
                end
                @(posedge clk);
                #1;
                for (i3 = 0; i3 < N_INST; i3 = i3 + 1) begin
                    if (!ov[i3]) begin
                        // A mutant may retire fewer samples; the vector
                        // comparison decides. Warn and keep streaming.
                        $display("TB-WARN inst %0d out_valid low at tick %0d", i3, t3);
                    end else begin
                        $fwrite(cap[run*N_INST + i3], "%0d %0d\n", raw_w[i3], post_w[i3]);
                    end
                end
                @(negedge clk);
            end

            $sformat(fname, "run%0d_ops.txt", run);
            fd = $fopen(fname, "w");
            for (i3 = 0; i3 < N_INST; i3 = i3 + 1)
                $fwrite(fd, "%0d %0d %0d %0d\n",
                    op_m[i3], op_n[i3], op_p[i3], op_c[i3]);
            $fclose(fd);
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

        for (r = 0; r < RUNS; r = r + 1) begin
            for (i = 0; i < N_INST; i = i + 1) begin
                $sformat(fname, "run%0d_captured%0d.txt", r, i);
                cap[r*N_INST + i] = $fopen(fname, "w");
                if (cap[r*N_INST + i] == 0) begin
                    $display("TB-ERROR cannot open %0s", fname);
                    $finish;
                end
            end
        end

        for (r = 0; r < RUNS; r = r + 1)
            run_one(r);

        for (r = 0; r < RUNS; r = r + 1)
            for (i = 0; i < N_INST; i = i + 1)
                $fclose(cap[r*N_INST + i]);

        $display("TB-DONE %0d", RUNS);
        $finish;
    end

endmodule
