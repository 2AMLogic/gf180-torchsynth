// SYNTHETIC harness DUT for the golden-vector self-test (issue #68).
//
// The 8-bit datapath width below is a property of this harness ONLY. It is
// NOT derived from the DR-0008 choice register (every C1-C10 entry refuses
// through require_accepted today) and this module makes NO conformance
// claim to any numeric contract, DR-0008 included.
//
// Function: registered XOR with a constant. Deliberately trivial; its only
// job is to give the harness a deterministic per-cycle stream to compare.

`timescale 1ns/1ps

module synth_dut (
    input  wire        clk,
    input  wire [7:0]  in_data,
    output reg  [7:0]  out_data
);

    always @(posedge clk) begin
        out_data <= in_data ^ 8'hA5;
    end

endmodule
