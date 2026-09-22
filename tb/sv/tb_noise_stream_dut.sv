// File-driven testbench over the noise-stream DUT (issue #75).
//
// Reads runs.txt (one integer: the number of streams to play), then for
// each run r: run<r>_stim.txt (three integers: sound_index, declared_slot,
// n_bytes) and run<r>_bytes.txt (hex, one byte per line, in feed order =
// little-endian sample order). Streams play back-to-back with `en` dropped
// for two cycles between runs — the DUT's per-stream state must clear
// through that gap (replay/reset carries no off-by-one state). Captured
// words are appended per run to run<r>_captured.txt; run<r>_status.txt
// carries "<error> <error_code> <bytes_accepted> <samples_produced>
// <narrow_count>". PDK-free: plain Icarus Verilog, no vendor or PDK cells.
// No floats participate anywhere; the Python runner (tb/run_tb.py 'noise')
// owns all expectations from the model's own primitives and the golden
// receipt.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer MAX_BYTES = SCHED_SAMPLES_PER_PASS * 4;
    localparam integer MAX_RUNS  = 64;

    reg                        clk = 1'b0;
    reg                        rst = 1'b1;
    reg                        en  = 1'b0;
    reg [C2_WIDTH-1:0]         sound_index = {C2_WIDTH{1'b0}};
    reg [4:0]                  declared_slot = 5'd0;
    reg [7:0]                  byte_in = 8'd0;
    reg                        byte_valid = 1'b0;
    reg                        in_done = 1'b0;
    wire signed [C1_WIDTH-1:0] sample_word;
    wire                       sample_valid;
    wire                       error;
    wire [7:0]                 error_code;
    wire [19:0]                bytes_accepted;
    wire [17:0]                samples_produced;
    wire [31:0]                narrow_count;

    noise_stream_dut dut (
        .clk             (clk),
        .rst             (rst),
        .en              (en),
        .sound_index     (sound_index),
        .declared_slot   (declared_slot),
        .byte_in         (byte_in),
        .byte_valid      (byte_valid),
        .in_done         (in_done),
        .sample_word     (sample_word),
        .sample_valid    (sample_valid),
        .error           (error),
        .error_code      (error_code),
        .bytes_accepted  (bytes_accepted),
        .samples_produced(samples_produced),
        .narrow_count    (narrow_count)
    );

    always #5 clk = ~clk;

    // One clip of host-fed binary32 noise bytes (reloaded per run).
    reg [7:0] bytes_mem [0:MAX_BYTES-1];

    reg [1023:0] fname;
    integer cap_fd [0:MAX_RUNS-1];
    integer status_fd [0:MAX_RUNS-1];
    integer RUNS;
    integer r;
    integer i;
    integer n_bytes;
    integer s_index;
    integer d_slot;
    integer fd;
    integer code;

    // Capture every produced sample word of the active run.
    always @(posedge clk) begin
        if (!rst && en && sample_valid && r >= 0)
            $fwrite(cap_fd[r], "%0d\n", sample_word);
    end

    task load_run(input integer run);
        begin
            $sformat(fname, "run%0d_stim.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end
            code = $fscanf(fd, "%d %d %d", s_index, d_slot, n_bytes);
            $fclose(fd);
            if (code != 3) begin
                $display("TB-ERROR %0s must carry 3 integers", fname);
                $finish;
            end
            if (n_bytes <= 0 || n_bytes > MAX_BYTES) begin
                $display("TB-ERROR byte count %0d outside 1..%0d", n_bytes, MAX_BYTES);
                $finish;
            end
            $sformat(fname, "run%0d_bytes.txt", run);
            $readmemh(fname, bytes_mem);
        end
    endtask

    task play_run(input integer run);
        integer k;
        begin
            sound_index   = s_index;
            declared_slot = d_slot[4:0];
            en            = 1'b1;
            // Feed one byte per enabled clock; the DUT samples at posedge.
            for (k = 0; k < n_bytes; k = k + 1) begin
                byte_in    = bytes_mem[k];
                byte_valid = 1'b1;
                @(negedge clk);
            end
            byte_valid = 1'b0;
            // One enabled cycle with in_done: the DUT latches the length
            // check (truncated streams fail here).
            in_done = 1'b1;
            @(negedge clk);
            in_done = 1'b0;
            @(negedge clk);
            // Capture the run status while the state is still live — the en
            // gap below clears the per-stream counters.
            $fwrite(status_fd[run], "%0d %0d %0d %0d %0d\n",
                    error, error_code, bytes_accepted, samples_produced,
                    narrow_count);
            // The en gap: per-stream state must clear between runs.
            en = 1'b0;
            @(negedge clk);
            @(negedge clk);
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
        rst = 1'b1;
        en  = 1'b0;
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
