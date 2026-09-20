// File-driven testbench over the SYNTHETIC harness DUT (issue #68).
//
// Reads stimulus.txt (one integer per line, cwd-relative), drives one value
// per clock cycle, and writes captured.txt (one output integer per cycle).
// Both file names are fixed so the Python runner (tb/run_tb.py) controls
// everything through the working directory. PDK-free: plain Icarus
// Verilog / Verilator-able source, no vendor or PDK cells.

`timescale 1ns/1ps

module tb;

    localparam integer MAX_CYCLES = 4096;

    reg         clk = 1'b0;
    reg  [7:0]  in_data = 8'h00;
    wire [7:0]  out_data;

    synth_dut dut (
        .clk      (clk),
        .in_data  (in_data),
        .out_data (out_data)
    );

    always #5 clk = ~clk;

    integer fd;
    integer ofd;
    integer code;
    integer value;
    integer stim [0:MAX_CYCLES-1];
    integer n_stim;
    integer n;

    initial begin
        fd = $fopen("stimulus.txt", "r");
        if (fd == 0) begin
            $display("TB-ERROR cannot open stimulus.txt");
            $finish;
        end
        n_stim = 0;
        while (!$feof(fd) && n_stim < MAX_CYCLES) begin
            code = $fscanf(fd, "%d", value);
            if (code == 1) begin
                stim[n_stim] = value;
                n_stim = n_stim + 1;
            end
        end
        $fclose(fd);
        if (n_stim == 0) begin
            $display("TB-ERROR empty stimulus");
            $finish;
        end

        ofd = $fopen("captured.txt", "w");
        if (ofd == 0) begin
            $display("TB-ERROR cannot open captured.txt");
            $finish;
        end
        for (n = 0; n < n_stim; n = n + 1) begin
            @(negedge clk);
            in_data = stim[n][7:0];
            @(posedge clk);
            #1;
            $fwrite(ofd, "%0d\n", out_data);
        end
        $fclose(ofd);
        $display("TB-DONE %0d", n_stim);
        $finish;
    end

endmodule
