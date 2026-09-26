// tb_render_binding.sv — file-driven testbench over render_binding_top
// (issue #211): does a protocol-level rejection actually discard the clip
// inside the normalization replay engine?
//
// Reads runs.txt (one integer: the number of runs), then for each run r:
//
//   run<r>_stim.txt   one integer: the per-pass mix sample count (normally
//                     SCHED_SAMPLES_PER_PASS)
//   run<r>_proto_a.txt  per-cycle protocol schedule driven BEFORE pass 1 is
//                     fed ("B <decimal byte>" presents a command byte, "I"
//                     idles) — HELLO with CAP_RENDER, a full 78-name patch
//                     commit, the pass-1 RENDER_TRIGGER and that pass's
//                     whole NOISE_STREAM
//   run<r>_proto_b.txt  per-cycle protocol schedule driven at the pass-1 /
//                     pass-2 boundary — the pass-2 RENDER_TRIGGER, whose
//                     declared-digest check either binds or rejects
//   run<r>_mix.hex     $readmemh, one 24-bit two's-complement Q2.21
//                     pre-normalization mix word per line, fed verbatim for
//                     BOTH passes (DR-0010's idempotent re-feed)
//
// Per run: reset, play proto_a, pulse `start`, feed pass 1 + mix_done, play
// proto_b (the receiver's bind_reject pulse, if any, lands here while the
// engine sits in PASS2), then feed pass 2 + mix_done. Writes
// run<r>_captured.txt (every released audio_out word — pass 2 only),
// run<r>_rsp.txt (the receiver's whole response byte stream) and
// run<r>_status.txt: "<error> <error_code> <done> <pass_index>
// <bind_pulses> <bind_max_run> <session>".
//
// Each run is reset-separated on purpose: the engine's ERR_BINDING_REJECTED
// is sticky until `rst`, and each run needs its own negotiated session and
// committed patch anyway. tb/run_tb.py 'normreplay' owns every expectation.
// PDK-free: plain Icarus Verilog, no vendor or PDK cells, no floats.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb_render_binding;

    // The receiver's per-pass noise-stream length. Scaled down by default
    // so proto_a stays a few thousand cycles: this bench demonstrates the
    // bind_reject path, and the product 705,600-B length is completed
    // end-to-end by tb/run_tb.py 'patch' instead.
    parameter integer NOISE_CLIP_BYTES = 2048;

    localparam integer MAX_SAMPLES = SCHED_SAMPLES_PER_PASS + 8;
    localparam integer MAX_RUNS = 8;

    reg clk = 1'b0;
    reg rst = 1'b1;

    reg        cmd_valid = 1'b0;
    reg [7:0]  cmd_byte = 8'h00;
    reg        rsp_ready = 1'b1;
    reg        start = 1'b0;
    reg        abort = 1'b0;
    reg signed [C1_WIDTH-1:0] mix_in = {C1_WIDTH{1'b0}};
    reg        mix_valid = 1'b0;
    reg        mix_done = 1'b0;

    wire       cmd_ready;
    wire       rsp_valid;
    wire [7:0] rsp_byte;
    wire [1:0] state_out;
    wire       patch_active;
    wire       bind_reject;
    wire signed [C1_WIDTH-1:0] audio_out;
    wire       audio_out_valid;
    wire       busy;
    wire       done;
    wire       error;
    wire [7:0] error_code;
    wire [1:0] pass_index;
    wire [17:0] samples_this_pass;
    wire       branch_normalized;
    wire [C1_WIDTH-1:0] peak_word;
    wire [C9_WIDTH-1:0] gain_word;

    render_binding_top #(
        .NOISE_CLIP_BYTES(NOISE_CLIP_BYTES)
    ) dut (
        .clk               (clk),
        .rst               (rst),
        .cmd_valid         (cmd_valid),
        .cmd_byte          (cmd_byte),
        .cmd_ready         (cmd_ready),
        .rsp_valid         (rsp_valid),
        .rsp_byte          (rsp_byte),
        .rsp_ready         (rsp_ready),
        .state_out         (state_out),
        .patch_active      (patch_active),
        .bind_reject       (bind_reject),
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
        .gain_word         (gain_word)
    );

    always #5 clk = ~clk;

    reg signed [C1_WIDTH-1:0] mix_mem [0:MAX_SAMPLES-1];

    integer cap_fd [0:MAX_RUNS-1];
    integer rsp_fd [0:MAX_RUNS-1];
    integer status_fd [0:MAX_RUNS-1];
    integer RUNS;
    integer r;
    integer nsamples;
    integer fd;
    integer code;
    integer value;
    reg [1023:0] fname;
    reg [1023:0] line;
    reg stim_eof;

    integer bind_pulses;
    integer bind_run;
    integer bind_max_run;

    // Released audio words and response bytes of the active run.
    always @(posedge clk) begin
        if (!rst && r >= 0) begin
            if (audio_out_valid)
                $fwrite(cap_fd[r], "%0d\n", audio_out);
            if (rsp_valid && rsp_ready)
                $fwrite(rsp_fd[r], "%0d\n", rsp_byte);
        end
    end

    // bind_reject observation: pulse count + longest consecutive high run.
    always @(posedge clk) begin
        if (rst) begin
            bind_pulses = 0;
            bind_run = 0;
            bind_max_run = 0;
        end else if (bind_reject) begin
            if (bind_run == 0)
                bind_pulses = bind_pulses + 1;
            bind_run = bind_run + 1;
            if (bind_run > bind_max_run)
                bind_max_run = bind_run;
        end else begin
            bind_run = 0;
        end
    end

    task play_stim(input [1023:0] path);
        integer sf;
        begin
            sf = $fopen(path, "r");
            if (sf == 0) begin
                $display("TB-ERROR cannot open %0s", path);
                $finish;
            end
            stim_eof = 1'b0;
            while (!$feof(sf) && !stim_eof) begin
                code = $fgets(line, sf);
                if (code == 0) begin
                    stim_eof = 1'b1;
                end else begin
                    // Icarus $fgets right-justifies: the line's first
                    // character sits at (code-1)*8 (code counts the newline)
                    if (line[((code-1)*8) +: 8] == "B") begin
                        $sscanf(line, "B %d", value);
                        cmd_valid <= 1'b1;
                        cmd_byte  <= value[7:0];
                    end else begin
                        cmd_valid <= 1'b0;
                    end
                    @(negedge clk);
                end
            end
            $fclose(sf);
            cmd_valid <= 1'b0;
            @(negedge clk);
        end
    endtask

    task feed_pass;
        integer k;
        begin
            for (k = 0; k < nsamples; k = k + 1) begin
                mix_in    = mix_mem[k];
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

    task load_run(input integer run);
        begin
            $sformat(fname, "run%0d_stim.txt", run);
            fd = $fopen(fname, "r");
            if (fd == 0) begin
                $display("TB-ERROR cannot open %0s", fname);
                $finish;
            end
            code = $fscanf(fd, "%d", nsamples);
            $fclose(fd);
            if (code != 1 || nsamples < 0 || nsamples > MAX_SAMPLES) begin
                $display("TB-ERROR bad sample count in %0s", fname);
                $finish;
            end
            $sformat(fname, "run%0d_mix.hex", run);
            $readmemh(fname, mix_mem);
        end
    endtask

    task play_run(input integer run);
        begin
            // Each run gets its own reset: the engine's sticky
            // ERR_BINDING_REJECTED clears only on rst, and the receiver
            // needs a fresh negotiated session and committed patch.
            rst = 1'b1;
            @(negedge clk);
            @(negedge clk);
            rst = 1'b0;
            @(negedge clk);

            $sformat(fname, "run%0d_proto_a.txt", run);
            play_stim(fname);

            start = 1'b1;
            @(negedge clk);
            start = 1'b0;
            feed_pass;               // pass 1: peak tracking

            $sformat(fname, "run%0d_proto_b.txt", run);
            play_stim(fname);        // the pass-2 trigger lands here

            feed_pass;               // pass 2: replay (or nothing, if rejected)

            @(negedge clk);
            $fwrite(status_fd[run], "%0d %0d %0d %0d %0d %0d %0d\n",
                    error, error_code, done, pass_index,
                    bind_pulses, bind_max_run, state_out);
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
            $sformat(fname, "run%0d_rsp.txt", r);
            rsp_fd[r] = $fopen(fname, "w");
            $sformat(fname, "run%0d_status.txt", r);
            status_fd[r] = $fopen(fname, "w");
            if (cap_fd[r] == 0 || rsp_fd[r] == 0 || status_fd[r] == 0) begin
                $display("TB-ERROR cannot open run %0d capture files", r);
                $finish;
            end
        end

        r = -1;
        rst = 1'b1;
        @(negedge clk);

        for (r = 0; r < RUNS; r = r + 1) begin
            load_run(r);
            play_run(r);
        end

        for (r = 0; r < RUNS; r = r + 1) begin
            $fclose(cap_fd[r]);
            $fclose(rsp_fd[r]);
            $fclose(status_fd[r]);
        end
        $display("TB-DONE %0d", RUNS);
        $finish;
    end

endmodule
