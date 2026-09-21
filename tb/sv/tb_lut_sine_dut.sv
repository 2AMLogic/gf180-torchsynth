// File-driven testbench over the format-true LUT-sine DUT (issue #68).
//
// Reads stimulus.txt (cwd-relative, three integers: phase-step word, mixer
// level word, sample count), drives one enabled sample per clock, and
// writes captured.txt as "<vco_word> <mix_word>" per line. The LUT payload
// arrives via the +lut=<memh> plusarg so the Python runner (tb/run_tb.py)
// controls everything, including the temp working directory. PDK-free:
// plain Icarus Verilog, no vendor or PDK cells.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb;

    localparam integer MAX_CYCLES = 262144;

    reg                        clk = 1'b0;
    reg                        rst = 1'b1;
    reg                        en  = 1'b0;
    reg [C2_WIDTH-1:0]         phase_step = {C2_WIDTH{1'b0}};
    reg signed [C1_WIDTH-1:0]  level = {C1_WIDTH{1'b0}};
    wire signed [C1_WIDTH-1:0] vco_word;
    wire signed [C1_WIDTH-1:0] mix_word;
    wire                       out_valid;

    lut_sine_dut dut (
        .clk        (clk),
        .rst        (rst),
        .en         (en),
        .phase_step (phase_step),
        .level      (level),
        .vco_word   (vco_word),
        .mix_word   (mix_word),
        .out_valid  (out_valid)
    );

    always #5 clk = ~clk;

    integer fd;
    integer ofd;
    integer code;
    integer k_step;
    integer lvl;
    integer n_samples;
    integer i;

    initial begin
        fd = $fopen("stimulus.txt", "r");
        if (fd == 0) begin
            $display("TB-ERROR cannot open stimulus.txt");
            $finish;
        end
        code = $fscanf(fd, "%d", k_step);
        code = code + $fscanf(fd, "%d", lvl);
        code = code + $fscanf(fd, "%d", n_samples);
        $fclose(fd);
        if (code != 3) begin
            $display("TB-ERROR stimulus.txt must carry 3 integers");
            $finish;
        end
        if (n_samples <= 0 || n_samples > MAX_CYCLES) begin
            $display("TB-ERROR sample count %0d outside 1..%0d", n_samples, MAX_CYCLES);
            $finish;
        end

        phase_step = k_step;
        level      = lvl;

        rst = 1'b1;
        en  = 1'b0;
        @(negedge clk);
        @(negedge clk);
        rst = 1'b0;
        en  = 1'b1;

        ofd = $fopen("captured.txt", "w");
        if (ofd == 0) begin
            $display("TB-ERROR cannot open captured.txt");
            $finish;
        end
        for (i = 0; i < n_samples; i = i + 1) begin
            @(posedge clk);
            #1;
            $fwrite(ofd, "%0d %0d\n", $signed(vco_word), $signed(mix_word));
        end
        $fclose(ofd);
        $display("TB-DONE %0d", n_samples);
        $finish;
    end

endmodule
