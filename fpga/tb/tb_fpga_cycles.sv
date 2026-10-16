// Cycle-count check for the measurement shim (issue #80, DR-0013).
// Counts clocks from start_pin until done_pin (or the shim's S_FIN state, to
// also cover short WALK values where the core flags a truncated pass).
// This checks the SHIM's schedule only; stimulus is all-zero config and
// all-zero noise bytes. It is not a numeric-correctness test.
`timescale 1ns/1ps
module tb_fpga_cycles;
    parameter integer WALK = 176400;
    reg clk = 0, rst_n = 0, start_pin = 0;
    wire done_pin, error_pin, sig_pin;
    fpga_oneshot_top #(.WALK(WALK), .SIM_SEED(0)) dut (
        .clk(clk), .rst_n(rst_n), .start_pin(start_pin), .cfg_in(1'b0),
        .cfg_shift(1'b0), .done_pin(done_pin), .error_pin(error_pin),
        .sig_pin(sig_pin));
    always #5 clk = ~clk;
    integer cyc = 0, fin_cyc = 0;
    reg counting = 0;
    always @(posedge clk) if (counting) cyc <= cyc + 1;
    initial begin
        dut.cfg_q = 0;
        repeat (4) @(posedge clk);
        rst_n = 1;
        repeat (4) @(posedge clk);
        start_pin = 1; counting = 1;
        @(posedge clk); start_pin = 0;
        begin : w
            integer n; n = 0;
            while (!done_pin && dut.st != 5'd13 && n < 3000000) begin @(posedge clk); n = n + 1; end
        end
        fin_cyc = cyc;
        repeat (3) @(posedge clk);   // done_pin/error_pin are registered
        $display("WALK=%0d cycles_start_to_fin_or_done=%0d expected_incl_start_cycle=%0d done=%b error=%b",
                 WALK, fin_cyc, 3 + 4*1764 + 1 + 2*(11 + 4*WALK), done_pin, error_pin);
        $display("st=%0d noise_error=%b noise_code=%0d core_error=%b core_code=%0d bytes=%0d",
                 dut.st, dut.noise_error, dut.noise_error_code, dut.error,
                 dut.error_code, dut.noise_bytes_accepted);
        $finish;
    end
endmodule
