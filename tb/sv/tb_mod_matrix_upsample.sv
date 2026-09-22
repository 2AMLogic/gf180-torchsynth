// File-driven testbench over the modulation-matrix + endpoint-aligned
// upsample engines (issue #72).
//
// Run protocol (paths cwd-relative, controlled by tb/run_tb.py):
// - runs.txt: one integer, the number of back-to-back runs R.
// - Per run r in 0..R-1:
//   - run<r>_depths.txt: five lines, one per route (vco_1_pitch,
//     vco_1_amp, vco_2_pitch, vco_2_amp, noise_amp), four signed decimal
//     C4 Q10.21 depth words each in the pinned source order
//     (adsr_1, adsr_2, lfo_1, lfo_2), written as unsigned 32-bit bit
//     patterns (read with %d and sliced, like the #70/#71 words).
//   - run<r>_columns.txt: CONTROL_SAMPLES lines of four signed Q2.21
//     control words: the matrix source columns in the pinned input order
//     (main ADSR1, main ADSR2, post-VCA LFO1, post-VCA LFO2), the #70/#71
//     engines' declared outputs, written as unsigned 32-bit bit patterns.
//   - run<r>_matrix.txt: CONTROL_SAMPLES lines of five signed C1 Q2.21
//     words, the matrix outputs in route order.
//   - run<r>_audio<i>.txt for i = 0..4: AUDIO_SAMPLES lines of one signed
//     C1 Q2.21 word each, the five endpoint-aligned upsample streams.
//   - run<r>_ops.txt: six lines —
//       "M mults adds narrows sats" (the matrix engine)
//       "U mults adds narrows fracwords coordsteps sats outcount"
//       (per upsample engine, route order).
//
// Phases per run: (1) the matrix engine walks the 1764 control ticks with
// the streamed source columns; (2) the tb loads each captured route
// column into its upsample engine (1764 load cycles); (3) one go pulse
// starts all five audio walks concurrently (176,400 emissions each).
// Every run triggers fresh: no cross-run or cross-instance state may
// survive a trigger. PDK-free Icarus Verilog, no vendor or PDK cells.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer CONTROL_SAMPLES = 1764;
    localparam integer AUDIO_SAMPLES   = 176400;
    localparam integer N_ROUTES        = 5;
    localparam integer MAX_RUNS        = 8;

    reg clk = 1'b0;
    reg rst = 1'b1;
    reg en  = 1'b0;

    always #5 clk = ~clk;

    reg        mm_trig;
    reg [4*C4_WIDTH-1:0] depths [0:N_ROUTES-1];
    reg signed [C1_WIDTH-1:0] col_adsr_1, col_adsr_2, col_lfo_1, col_lfo_2;

    wire signed [C1_WIDTH-1:0] mm_out [0:N_ROUTES-1];
    wire                       mm_valid;
    wire [31:0] mm_op_m, mm_op_a, mm_op_n, mm_op_s;

    mod_matrix_engine mm_eng (
        .clk        (clk),
        .rst        (rst),
        .en         (en),
        .trigger    (mm_trig),
        .depths_r0  (depths[0]),
        .depths_r1  (depths[1]),
        .depths_r2  (depths[2]),
        .depths_r3  (depths[3]),
        .depths_r4  (depths[4]),
        .col_adsr_1 (col_adsr_1),
        .col_adsr_2 (col_adsr_2),
        .col_lfo_1  (col_lfo_1),
        .col_lfo_2  (col_lfo_2),
        .out_r0     (mm_out[0]),
        .out_r1     (mm_out[1]),
        .out_r2     (mm_out[2]),
        .out_r3     (mm_out[3]),
        .out_r4     (mm_out[4]),
        .out_valid  (mm_valid),
        .op_mults   (mm_op_m),
        .op_adds    (mm_op_a),
        .op_narrows (mm_op_n),
        .op_sats    (mm_op_s)
    );

    reg         up_load;
    reg [10:0]  up_load_index;
    reg         up_go;
    reg signed [C1_WIDTH-1:0] up_col [0:N_ROUTES-1];
    wire signed [C1_WIDTH-1:0] up_word  [0:N_ROUTES-1];
    wire                        up_valid [0:N_ROUTES-1];
    wire [31:0] up_op_m [0:N_ROUTES-1];
    wire [31:0] up_op_a [0:N_ROUTES-1];
    wire [31:0] up_op_n [0:N_ROUTES-1];
    wire [31:0] up_op_f [0:N_ROUTES-1];
    wire [31:0] up_op_c [0:N_ROUTES-1];
    wire [31:0] up_op_s [0:N_ROUTES-1];
    wire [31:0] up_cnt  [0:N_ROUTES-1];
    wire [31:0] up_idx  [0:N_ROUTES-1];

    genvar gi;
    generate
        for (gi = 0; gi < N_ROUTES; gi = gi + 1) begin : UP
            upsample_engine #(.ROUTE_ID(gi)) eng (
                .clk         (clk),
                .rst         (rst),
                .en          (en),
                .trigger     (mm_trig),
                .load        (up_load),
                .load_index  (up_load_index),
                .column_word (up_col[gi]),
                .go          (up_go),
                .audio_word  (up_word[gi]),
                .audio_valid (up_valid[gi]),
                .out_index   (up_idx[gi]),
                .op_mults    (up_op_m[gi]),
                .op_adds     (up_op_a[gi]),
                .op_narrows  (up_op_n[gi]),
                .op_frac_words (up_op_f[gi]),
                .op_coord_steps (up_op_c[gi]),
                .op_sats     (up_op_s[gi]),
                .out_count   (up_cnt[gi])
            );
        end
    endgenerate

    // Captured matrix columns for the current run: route-major flat array.
    reg signed [C1_WIDTH-1:0] cap_col [0:N_ROUTES*CONTROL_SAMPLES-1];
    // Column stimulus rows parked from the load task for the streaming loop.
    reg signed [C1_WIDTH-1:0] cols_tmp [0:4*CONTROL_SAMPLES-1];

    integer cap_audio [0:MAX_RUNS*N_ROUTES-1];

    integer RUNS;
    integer r, i, t;
    integer fd;
    integer code;
    integer v0, v1, v2, v3;
    reg [1023:0] fname;

    task load_run(input integer run);
        integer k2, t2;
        begin
            $sformat(fname, "run%0d_depths.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end
            for (k2 = 0; k2 < N_ROUTES; k2 = k2 + 1) begin
                code = $fscanf(fd, "%d %d %d %d", v0, v1, v2, v3);
                if (code != 4) begin
                    $display("TB-ERROR short depths line (route %0d, code %0d)", k2, code);
                    $finish;
                end
                depths[k2] = {
                    v3[C4_WIDTH-1:0], v2[C4_WIDTH-1:0],
                    v1[C4_WIDTH-1:0], v0[C4_WIDTH-1:0]
                };
            end
            $fclose(fd);
            $sformat(fname, "run%0d_columns.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end
            for (t2 = 0; t2 < CONTROL_SAMPLES; t2 = t2 + 1) begin
                code = $fscanf(fd, "%d %d %d %d", v0, v1, v2, v3);
                if (code != 4) begin
                    $display("TB-ERROR short columns row (tick %0d, code %0d)", t2, code);
                    $finish;
                end
                cols_tmp[t2*4 + 0] = v0[C1_WIDTH-1:0];
                cols_tmp[t2*4 + 1] = v1[C1_WIDTH-1:0];
                cols_tmp[t2*4 + 2] = v2[C1_WIDTH-1:0];
                cols_tmp[t2*4 + 3] = v3[C1_WIDTH-1:0];
            end
            $fclose(fd);
        end
    endtask

    task run_one(input integer run);
        integer k3, t3, w3;
        begin
            load_run(run);

            // ---- Phase 1: the matrix walk -----------------------------
            @(negedge clk);
            rst = 1'b0;
            en = 1'b1;
            mm_trig = 1'b1;
            up_load = 1'b0;
            up_go   = 1'b0;
            @(posedge clk);
            @(negedge clk);
            mm_trig = 1'b0;
            for (t3 = 0; t3 < CONTROL_SAMPLES; t3 = t3 + 1) begin
                col_adsr_1 = cols_tmp[t3*4 + 0];
                col_adsr_2 = cols_tmp[t3*4 + 1];
                col_lfo_1  = cols_tmp[t3*4 + 2];
                col_lfo_2  = cols_tmp[t3*4 + 3];
                @(posedge clk);
                #1;
                if (mm_valid) begin
                    for (k3 = 0; k3 < N_ROUTES; k3 = k3 + 1) begin
                        cap_col[k3*CONTROL_SAMPLES + t3] = mm_out[k3];
                    end
                end
                @(negedge clk);
            end

            $sformat(fname, "run%0d_matrix.txt", run);
            fd = $fopen(fname, "w");
            for (t3 = 0; t3 < CONTROL_SAMPLES; t3 = t3 + 1) begin
                $fwrite(fd, "%0d %0d %0d %0d %0d\n",
                    cap_col[0*CONTROL_SAMPLES + t3],
                    cap_col[1*CONTROL_SAMPLES + t3],
                    cap_col[2*CONTROL_SAMPLES + t3],
                    cap_col[3*CONTROL_SAMPLES + t3],
                    cap_col[4*CONTROL_SAMPLES + t3]);
            end
            $fclose(fd);

            // ---- Phase 2: column load into the upsample engines -------
            for (t3 = 0; t3 < CONTROL_SAMPLES; t3 = t3 + 1) begin
                for (k3 = 0; k3 < N_ROUTES; k3 = k3 + 1) begin
                    up_col[k3] = cap_col[k3*CONTROL_SAMPLES + t3];
                end
                up_load = 1'b1;
                up_load_index = t3[10:0];
                @(posedge clk);
                @(negedge clk);
            end
            up_load = 1'b0;

            // ---- Phase 3: the five audio walks ------------------------
            up_go = 1'b1;
            @(posedge clk);
            @(negedge clk);
            up_go = 1'b0;
            for (w3 = 0; w3 < AUDIO_SAMPLES; w3 = w3 + 1) begin
                @(posedge clk);
                #1;
                for (k3 = 0; k3 < N_ROUTES; k3 = k3 + 1) begin
                    if (up_valid[k3]) begin
                        $fwrite(cap_audio[run*N_ROUTES + k3], "%0d\n", up_word[k3]);
                    end
                end
                @(negedge clk);
            end

            $sformat(fname, "run%0d_ops.txt", run);
            fd = $fopen(fname, "w");
            $fwrite(fd, "M %0d %0d %0d %0d\n",
                mm_op_m, mm_op_a, mm_op_n, mm_op_s);
            for (k3 = 0; k3 < N_ROUTES; k3 = k3 + 1) begin
                $fwrite(fd, "U %0d %0d %0d %0d %0d %0d %0d\n",
                    up_op_m[k3], up_op_a[k3], up_op_n[k3],
                    up_op_f[k3], up_op_c[k3], up_op_s[k3], up_cnt[k3]);
            end
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
            for (i = 0; i < N_ROUTES; i = i + 1) begin
                $sformat(fname, "run%0d_audio%0d.txt", r, i);
                cap_audio[r*N_ROUTES + i] = $fopen(fname, "w");
                if (cap_audio[r*N_ROUTES + i] == 0) begin
                    $display("TB-ERROR cannot open %0s", fname);
                    $finish;
                end
            end
        end

        for (r = 0; r < RUNS; r = r + 1)
            run_one(r);

        for (r = 0; r < RUNS; r = r + 1)
            for (i = 0; i < N_ROUTES; i = i + 1)
                $fclose(cap_audio[r*N_ROUTES + i]);

        $display("TB-DONE %0d", RUNS);
        $finish;
    end

endmodule
