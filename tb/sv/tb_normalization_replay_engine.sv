// File-driven testbench over the normalization replay controller (#77).
//
// Reads runs.txt (one integer: the number of clip renders to play), then
// for each run r: run<r>_stim.txt (two integers: n1 n2, the pass-1 and
// pass-2 declared sample counts — normally both
// SCHED_SAMPLES_PER_PASS, deliberately off by one for the framing-fault
// demonstrations), run<r>_mix1.hex and run<r>_mix2.hex ($readmemh, one
// 24-bit two's-complement Q2.21 word per line, feed order == sample
// order). Each run: `start` for one cycle from idle/done, feed n1 mix
// words with mix_valid held (one every cycle), one cycle with mix_valid
// low + mix_done high (the #75 noise engine's byte/in_done handshake
// shape, at sample granularity), then the same for n2 pass-2 words.
// Captured audio_out words (only ever produced in pass 2) are appended to
// run<r>_captured.txt; run<r>_status.txt carries one line: "<error>
// <error_code> <peak_word> <gain_word> <branch_normalized> <done>
// <pass_index> <op_compares> <op_selects> <op_recip_divs> <op_mults>
// <op_narrows> <op_saturations>". Runs play back-to-back with no reset in
// between (`start` from the prior run's `done`), demonstrating "no
// resumable render, no off-by-one state carried across triggers" per the
// DR-0010 clip lifecycle contract. PDK-free: plain Icarus Verilog, no
// vendor or PDK cells. No floats participate anywhere; the Python runner
// (tb/run_tb.py 'normreplay') owns all expectations from the frozen
// model's own primitives.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer OVERRUN_SLACK = 8;
    localparam integer MAX_SAMPLES = SCHED_SAMPLES_PER_PASS + OVERRUN_SLACK;
    localparam integer MAX_RUNS = 16;

    reg                        clk = 1'b0;
    reg                        rst = 1'b1;
    reg                        start = 1'b0;
    reg                        abort = 1'b0;
    reg  signed [C1_WIDTH-1:0] mix_in = {C1_WIDTH{1'b0}};
    reg                        mix_valid = 1'b0;
    reg                        mix_done = 1'b0;

    wire signed [C1_WIDTH-1:0] audio_out;
    wire                       audio_out_valid;
    wire                       busy;
    wire                       done;
    wire                       error;
    wire [7:0]                 error_code;
    wire [1:0]                 pass_index;
    wire [17:0]                samples_this_pass;
    wire                       branch_normalized;
    wire [C1_WIDTH-1:0]        peak_word;
    wire [C9_WIDTH-1:0]        gain_word;
    wire [31:0]                op_compares;
    wire [31:0]                op_selects;
    wire [31:0]                op_recip_divs;
    wire [31:0]                op_mults;
    wire [31:0]                op_narrows;
    wire [31:0]                op_saturations;

    normalization_replay_engine dut (
        .clk               (clk),
        .rst               (rst),
        .start             (start),
        .abort             (abort),
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
        .op_compares       (op_compares),
        .op_selects        (op_selects),
        .op_recip_divs     (op_recip_divs),
        .op_mults          (op_mults),
        .op_narrows        (op_narrows),
        .op_saturations    (op_saturations)
    );

    always #5 clk = ~clk;

    reg signed [C1_WIDTH-1:0] mix1_mem [0:MAX_SAMPLES-1];
    reg signed [C1_WIDTH-1:0] mix2_mem [0:MAX_SAMPLES-1];

    reg [1023:0] fname;
    integer cap_fd [0:MAX_RUNS-1];
    integer status_fd [0:MAX_RUNS-1];
    integer RUNS;
    integer r;
    integer n1, n2;
    integer fd;
    integer code;

    // Capture every produced audio sample of the active run (pass 2 only).
    always @(posedge clk) begin
        if (!rst && audio_out_valid && r >= 0)
            $fwrite(cap_fd[r], "%0d\n", audio_out);
    end

    task load_run(input integer run);
        begin
            $sformat(fname, "run%0d_stim.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end
            code = $fscanf(fd, "%d %d", n1, n2);
            $fclose(fd);
            if (code != 2) begin
                $display("TB-ERROR %0s must carry 2 integers", fname);
                $finish;
            end
            if (n1 < 0 || n1 > MAX_SAMPLES || n2 < 0 || n2 > MAX_SAMPLES) begin
                $display("TB-ERROR pass counts (%0d, %0d) outside 0..%0d",
                         n1, n2, MAX_SAMPLES);
                $finish;
            end
            $sformat(fname, "run%0d_mix1.hex", run);
            $readmemh(fname, mix1_mem);
            $sformat(fname, "run%0d_mix2.hex", run);
            $readmemh(fname, mix2_mem);
        end
    endtask

    task feed_pass1;
        integer k;
        begin
            for (k = 0; k < n1; k = k + 1) begin
                mix_in    = mix1_mem[k];
                mix_valid = 1'b1;
                @(negedge clk);
            end
            mix_valid = 1'b0;
            @(negedge clk);
            mix_done = 1'b1;
            @(negedge clk);
            mix_done = 1'b0;
            @(negedge clk);
        end
    endtask

    task feed_pass2;
        integer k;
        begin
            for (k = 0; k < n2; k = k + 1) begin
                mix_in    = mix2_mem[k];
                mix_valid = 1'b1;
                @(negedge clk);
            end
            mix_valid = 1'b0;
            @(negedge clk);
            mix_done = 1'b1;
            @(negedge clk);
            mix_done = 1'b0;
            @(negedge clk);
        end
    endtask

    task play_run(input integer run);
        begin
            start = 1'b1;
            @(negedge clk);
            start = 1'b0;
            feed_pass1;
            feed_pass2;
            // One settle cycle, then capture the final status line.
            @(negedge clk);
            $fwrite(status_fd[run], "%0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",
                    error, error_code, peak_word, gain_word, branch_normalized,
                    done, pass_index, op_compares, op_selects, op_recip_divs,
                    op_mults, op_narrows, op_saturations);
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
        if (code != 1 || RUNS <= 0 || RUNS > MAX_RUNS) begin
            $display("TB-ERROR bad run count (%0d)", RUNS);
            $finish;
        end

        for (r = 0; r < RUNS; r = r + 1) begin
            $sformat(fname, "run%0d_captured.txt", r);
            cap_fd[r] = $fopen(fname, "w");
            $sformat(fname, "run%0d_status.txt", r);
            status_fd[r] = $fopen(fname, "w");
            if (cap_fd[r] == 0 || status_fd[r] == 0) begin
                $display("TB-ERROR cannot open run %0d capture files", r);
                $finish;
            end
        end

        r = -1;
        rst   = 1'b1;
        start = 1'b0;
        abort = 1'b0;
        @(negedge clk);
        @(negedge clk);
        rst = 1'b0;
        @(negedge clk);

        for (r = 0; r < RUNS; r = r + 1) begin
            load_run(r);
            play_run(r);
        end

        for (r = 0; r < RUNS; r = r + 1) begin
            $fclose(cap_fd[r]);
            $fclose(status_fd[r]);
        end
        $display("TB-DONE %0d", RUNS);
        $finish;
    end

endmodule
