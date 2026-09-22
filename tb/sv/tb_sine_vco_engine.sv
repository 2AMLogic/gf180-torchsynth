// File-driven testbench over the sine VCO engine (issue #73).
//
// Run protocol (paths cwd-relative, controlled by tb/run_tb.py):
// - runs.txt: one integer, the number of back-to-back runs R.
// - Per run r in 0..R-1:
//   - run<r>_params.txt: one line, four signed decimal words written as
//     unsigned 32-bit bit patterns (read with %d and sliced, like the
//     #70/#71/#72 words): midi_f0 (C4 Q10.21), tuning (C4), depth (C4),
//     initial_phase (C2 turn word).
//   - run<r>_streams.txt: one line per audio sample, "pitch fq": the
//     #72 endpoint-aligned upsample pitch column (C1 Q2.21) and the
//     host-shadow Q16.15 frequency word (the declared binary64 exp2
//     shadow site, derived host-side per DR-0008).
//   - run<r>_captured.txt: one line per emitted sample, "vco phase":
//     the vco_1.raw word (C1 Q2.21) and the post-step phase (C2).
//   - run<r>_cycles.txt: one integer, the enabled walk cycles (the
//     complete-clip schedule measurement).
//   - run<r>_ops.txt: one line "V mults adds narrows shadows sats clamps".
//
// The +max_n=<int> plusarg caps the audio walk — a mutation
// demonstration knob only; committed-case runs walk the full 176,400
// samples. Every run triggers fresh: no cross-run state may survive a
// trigger (the initial phase is re-injected per trigger). PDK-free
// Icarus Verilog, no vendor or PDK cells.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer AUDIO_SAMPLES = 176400;
    localparam integer MAX_RUNS      = 8;

    reg clk = 1'b0;
    reg rst = 1'b1;
    reg en  = 1'b0;

    always #5 clk = ~clk;

    reg        vco_trig;
    reg [C4_WIDTH-1:0] midi_f0_word, tuning_word, depth_word;
    reg [C2_WIDTH-1:0] initial_phase_word;
    reg signed [C1_WIDTH-1:0] up_pitch;
    reg signed [C3_WIDTH-1:0] fq_word;

    wire signed [C1_WIDTH-1:0] vco_word;
    wire        [C2_WIDTH-1:0] phase_out;
    wire                       vco_valid;
    wire [31:0] op_m, op_a, op_n, op_sh, op_s, op_c;

    integer max_n;

    sine_vco_engine eng (
        .clk                (clk),
        .rst                (rst),
        .en                 (en),
        .trigger            (vco_trig),
        .midi_f0_word       (midi_f0_word),
        .tuning_word        (tuning_word),
        .depth_word         (depth_word),
        .initial_phase_word (initial_phase_word),
        .up_pitch           (up_pitch),
        .fq_word            (fq_word),
        .vco_word           (vco_word),
        .phase_out          (phase_out),
        .out_valid          (vco_valid),
        .op_mults           (op_m),
        .op_adds            (op_a),
        .op_narrows         (op_n),
        .op_shadows         (op_sh),
        .op_sats            (op_s),
        .op_clamps          (op_c)
    );

    integer cap_fd [0:MAX_RUNS-1];
    integer RUNS;
    integer r, w;
    integer fd;
    integer code;
    integer v0, v1;
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
            code = $fscanf(fd, "%d %d %d %d",
                midi_f0_word, tuning_word, depth_word, initial_phase_word);
            if (code != 4) begin
                $display("TB-ERROR short params line (code %0d)", code);
                $finish;
            end
            $fclose(fd);

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
            vco_trig = 1'b1;
            @(posedge clk);
            @(negedge clk);
            vco_trig = 1'b0;
            walked = 0;
            while (walked < max_n) begin
                code = $fscanf(fd, "%d %d", v0, v1);
                if (code != 2) begin
                    $display("TB-ERROR short streams line (sample %0d, code %0d)", walked, code);
                    $finish;
                end
                up_pitch = v0[C1_WIDTH-1:0];
                fq_word  = v1[C3_WIDTH-1:0];
                @(posedge clk);
                #1;
                if (vco_valid) begin
                    $fwrite(cap_fd[run], "%0d %0d\n", vco_word, phase_out);
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
            $fwrite(fd, "V %0d %0d %0d %0d %0d %0d\n",
                op_m, op_a, op_n, op_sh, op_s, op_c);
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
