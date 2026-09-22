// File-driven testbench over the six-instance ADSR engine (issue #70).
//
// Run protocol (paths cwd-relative, controlled by tb/run_tb.py):
// - runs.txt: one integer, the number of back-to-back runs R.
// - Per run r in 0..R-1:
//   - run<r>_params.txt: six lines, one per instance, fifteen integers:
//       dur_lo dur_hi atk_lo atk_hi dec_lo dec_hi rel_lo rel_hi
//       fdur fatk fdec frel sustain eps_lo eps_hi
//     Q16.30 length words arrive as (lo, hi) 32-bit halves (hi*2^32+lo);
//     f* are the exact pre-quantization zero flags (0/1); sustain is the
//     signed C4 Q10.21 entry word; eps is the declared Q2.60 shadow
//     epsilon as (lo, hi) halves.
//   - run<r>_shadow<i>.txt: CONTROL_SAMPLES lines of three signed
//     integers: the per-tick post-power Q2.30 shadow words (attack, decay,
//     release) replayed host-side by the tb flow.
//   - run<r>_captured<i>.txt: CONTROL_SAMPLES captured envelope words
//     (signed C1 Q2.21 decimal).
//   - run<r>_ops.txt: six lines "mults narrows shadow rampdivs".
//
// All six engine instances run concurrently; every run triggers fresh
// (no prior-envelope state may survive a trigger). PDK-free Icarus
// Verilog, no vendor or PDK cells.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer N_INST = 6;
    localparam integer CONTROL_SAMPLES = 1764;
    localparam integer MAX_RUNS = 8;

    reg clk = 1'b0;
    reg rst = 1'b1;
    reg en = 1'b0;

    always #5 clk = ~clk;

    reg                    trig [0:N_INST-1];
    reg signed [46:0]      dur_q   [0:N_INST-1];
    reg signed [46:0]      atk_q   [0:N_INST-1];
    reg signed [46:0]      dec_q   [0:N_INST-1];
    reg signed [46:0]      rel_q   [0:N_INST-1];
    reg                    fz_d    [0:N_INST-1];
    reg                    fz_a    [0:N_INST-1];
    reg                    fz_c    [0:N_INST-1];
    reg                    fz_r    [0:N_INST-1];
    reg signed [31:0]      sustain [0:N_INST-1];
    reg signed [62:0]      eps60   [0:N_INST-1];
    reg signed [31:0]      sh_a    [0:N_INST-1];
    reg signed [31:0]      sh_d    [0:N_INST-1];
    reg signed [31:0]      sh_r    [0:N_INST-1];

    wire signed [23:0]     env   [0:N_INST-1];
    wire                   ov    [0:N_INST-1];
    wire [31:0]            op_m  [0:N_INST-1];
    wire [31:0]            op_n  [0:N_INST-1];
    wire [31:0]            op_s  [0:N_INST-1];
    wire [31:0]            op_rd [0:N_INST-1];

    genvar gi;
    generate
        for (gi = 0; gi < N_INST; gi = gi + 1) begin : ENG
            adsr_engine #(.INST_ID(gi)) eng (
                .clk                (clk),
                .rst                (rst),
                .en                 (en),
                .trigger            (trig[gi]),
                .duration_q         (dur_q[gi]),
                .attack_q           (atk_q[gi]),
                .decay_q            (dec_q[gi]),
                .release_q          (rel_q[gi]),
                .duration_exact_zero(fz_d[gi]),
                .attack_exact_zero  (fz_a[gi]),
                .decay_exact_zero   (fz_c[gi]),
                .release_exact_zero (fz_r[gi]),
                .sustain_entry      (sustain[gi]),
                .eps60              (eps60[gi]),
                .shadow_attack      (sh_a[gi]),
                .shadow_decay       (sh_d[gi]),
                .shadow_release     (sh_r[gi]),
                .env_word           (env[gi]),
                .out_valid          (ov[gi]),
                .op_mults           (op_m[gi]),
                .op_narrows         (op_n[gi]),
                .op_shadow          (op_s[gi]),
                .op_rampdivs        (op_rd[gi])
            );
        end
    endgenerate

    // Shadow streams for the current run: instance-major flat arrays.
    reg signed [31:0] mem_a [0:N_INST*CONTROL_SAMPLES-1];
    reg signed [31:0] mem_d [0:N_INST*CONTROL_SAMPLES-1];
    reg signed [31:0] mem_r [0:N_INST*CONTROL_SAMPLES-1];

    // One output stream per instance per run, opened up front.
    integer cap [0:MAX_RUNS*N_INST-1];

    integer RUNS;
    integer r;
    integer i;
    integer t;
    integer fd;
    integer code;
    integer v0, v1, v2, v3, v4, v5, v6, v7;
    integer v8, v9, v10, v11, v12, v13, v14;
    integer va, vd, vr;
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
                code = $fscanf(fd, "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d",
                    v0, v1, v2, v3, v4, v5, v6, v7,
                    v8, v9, v10, v11, v12, v13, v14);
                if (code != 15) begin
                    $display("TB-ERROR short params line (inst %0d, code %0d)", i2, code);
                    $finish;
                end
                dur_q[i2]   = {v1[14:0], v0[31:0]};
                atk_q[i2]   = {v3[14:0], v2[31:0]};
                dec_q[i2]   = {v5[14:0], v4[31:0]};
                rel_q[i2]   = {v7[14:0], v6[31:0]};
                fz_d[i2]    = v8[0];
                fz_a[i2]    = v9[0];
                fz_c[i2]    = v10[0];
                fz_r[i2]    = v11[0];
                sustain[i2] = v12[31:0];
                eps60[i2]   = {v14[30:0], v13[31:0]};
            end
            $fclose(fd);
            for (i2 = 0; i2 < N_INST; i2 = i2 + 1) begin
                $sformat(fname, "run%0d_shadow%0d.txt", run, i2);
                fd = $fopen(fname, "r");
                if (fd == 0) begin
                    $display("TB-ERROR cannot open %0s", fname);
                    $finish;
                end
                for (t2 = 0; t2 < CONTROL_SAMPLES; t2 = t2 + 1) begin
                    code = $fscanf(fd, "%d %d %d", va, vd, vr);
                    if (code != 3) begin
                        $display("TB-ERROR short shadow row (inst %0d tick %0d)", i2, t2);
                        $finish;
                    end
                    mem_a[i2*CONTROL_SAMPLES + t2] = va[31:0];
                    mem_d[i2*CONTROL_SAMPLES + t2] = vd[31:0];
                    mem_r[i2*CONTROL_SAMPLES + t2] = vr[31:0];
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

            // Trigger all six instances (one en cycle), then stream ticks.
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
                // Shadow words for the tick the engines hold in `index`.
                for (i3 = 0; i3 < N_INST; i3 = i3 + 1) begin
                    sh_a[i3] = mem_a[i3*CONTROL_SAMPLES + t3];
                    sh_d[i3] = mem_d[i3*CONTROL_SAMPLES + t3];
                    sh_r[i3] = mem_r[i3*CONTROL_SAMPLES + t3];
                end
                @(posedge clk);
                #1;
                for (i3 = 0; i3 < N_INST; i3 = i3 + 1) begin
                    if (!ov[i3]) begin
                        // A mutant may retire fewer samples; the vector
                        // comparison decides. Warn and keep streaming.
                        $display("TB-WARN inst %0d out_valid low at tick %0d", i3, t3);
                    end else begin
                        $fwrite(cap[run*N_INST + i3], "%0d\n", env[i3]);
                    end
                end
                @(negedge clk);
            end

            $sformat(fname, "run%0d_ops.txt", run);
            fd = $fopen(fname, "w");
            for (i3 = 0; i3 < N_INST; i3 = i3 + 1)
                $fwrite(fd, "%0d %0d %0d %0d\n",
                    op_m[i3], op_n[i3], op_s[i3], op_rd[i3]);
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
