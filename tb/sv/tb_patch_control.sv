// tb_patch_control.sv — PDK-free testbench for the patch-control core.
// Drives a per-cycle stimulus schedule (one line per clock cycle:
// "B <decimal byte>" presents a command byte, "I" idles) and captures the
// full response byte stream plus the final observable state (session,
// render gate, keyboard control words, stored sound identity, and the
// active-bank probe of all 78 slots).
//
// Plusargs: +stim=<path> +capture=<path> +final=<path>
//
// The stimulus schedules and every expectation are produced by the Python
// mirror (src/torchsynth_voice/patch_control_model.py) through
// tb/run_tb.py's `patch` command; this bench is byte-agnostic on purpose.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module tb_patch_control;

    reg clk = 1'b0;
    reg rst = 1'b1;

    reg        cmd_valid = 1'b0;
    reg [7:0]  cmd_byte = 8'h00;
    wire       cmd_ready;
    wire       rsp_valid;
    wire [7:0] rsp_byte;
    reg        rsp_ready = 1'b1;
    wire [1:0]  state_out;
    wire        patch_active;
    wire [C4_WIDTH-1:0] kbd_midi_f0_word;
    wire [46:0] kbd_duration_word;
    wire [7:0]  sound_identity_len;
    wire [511:0] sound_identity;
    reg  [6:0]  probe_slot = 7'd0;
    wire [C4_WIDTH-1:0] probe_value;
    wire        idle_out;

    patch_control dut (
        .clk(clk),
        .rst(rst),
        .cmd_valid(cmd_valid),
        .cmd_byte(cmd_byte),
        .cmd_ready(cmd_ready),
        .rsp_valid(rsp_valid),
        .rsp_byte(rsp_byte),
        .rsp_ready(rsp_ready),
        .state_out(state_out),
        .patch_active(patch_active),
        .kbd_midi_f0_word(kbd_midi_f0_word),
        .kbd_duration_word(kbd_duration_word),
        .sound_identity_len(sound_identity_len),
        .sound_identity(sound_identity),
        .probe_slot(probe_slot),
        .probe_value(probe_value),
        .idle_out(idle_out)
    );

    always #5 clk = ~clk;

    // response byte capture
    integer capf;
    always @(posedge clk) begin
        if (rsp_valid && rsp_ready)
            $fwrite(capf, "%0d\n", rsp_byte);
    end

    reg [1023:0] stim_path, cap_path, final_path;
    integer stimf, finalf;
    reg [1023:0] line;
    integer code, value;
    integer cycle;

    initial begin
        if (!$value$plusargs("stim=%s", stim_path)) begin
            $display("ERROR: +stim=<path> required");
            $finish;
        end
        if (!$value$plusargs("capture=%s", cap_path)) begin
            $display("ERROR: +capture=<path> required");
            $finish;
        end
        if (!$value$plusargs("final=%s", final_path)) begin
            $display("ERROR: +final=<path> required");
            $finish;
        end
        capf = $fopen(cap_path, "w");

        rst = 1'b1;
        repeat (4) @(negedge clk);
        rst = 1'b0;

        stimf = $fopen(stim_path, "r");
        if (stimf == 0) begin
            $display("ERROR: cannot open stimulus %0s", stim_path);
            $finish;
        end
        cycle = 0;
        while (!$feof(stimf)) begin
            code = $fgets(line, stimf);
            if (code == 0)
                break;
            // Icarus $fgets right-justifies: the first character of the
            // line sits at (code-1)*8 (code counts the newline)
            if (line[((code-1)*8) +: 8] == "B") begin
                $sscanf(line, "B %d", value);
                cmd_valid <= 1'b1;
                cmd_byte  <= value[7:0];
            end else begin
                cmd_valid <= 1'b0;
            end
            cycle = cycle + 1;
            @(negedge clk);
        end
        $fclose(stimf);
        cmd_valid <= 1'b0;

        // drain: let every outstanding walk, response, and builder byte
        // retire (bounded: the longest command walk + response frames)
        repeat (30000) @(negedge clk);

        // final observable state
        finalf = $fopen(final_path, "w");
        $fwrite(finalf, "%0d %0d %0d %0d %0d %0d\n",
                state_out, patch_active, kbd_midi_f0_word,
                kbd_duration_word, sound_identity_len, idle_out);
        begin : identity_dump
            integer k;
            for (k = 63; k >= 0; k = k - 1)
                $fwrite(finalf, "%02x", sound_identity[k*8 +: 8]);
            $fwrite(finalf, "\n");
        end
        begin : probe_dump
            integer s;
            for (s = 0; s < 78; s = s + 1) begin
                probe_slot = s[6:0];
                #1;
                $fwrite(finalf, "%0d\n", probe_value);
            end
        end
        $fclose(finalf);
        $fclose(capf);
        $display("TB DONE (%0d stimulus cycles)", cycle);
        $finish;
    end

endmodule
