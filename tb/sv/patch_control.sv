// patch_control.sv — RTL patch loader, identity, reset, keyboard controls.
// Issue #69 lane of DR-0010's module table ("Patch load, identity, reset,
// keyboard | #69 | control domain, once per trigger; S1 entry sites at
// trigger time").
//
// Implements the control-plane subset of:
//   spec/protocol/FRAMING.md    (protocol version 2 frame envelope, HELLO/
//                               READY negotiation, numeric-contract gate)
//   spec/protocol/PATCH-LOAD.md (name-keyed staged patch transaction over
//                               the 78-name canonical table, the
//                               patch-hash-v2 domain-separated SHA-256
//                               commit gate)
//   spec/protocol/SESSION.md    (lifecycle, ordering, backpressure,
//                               timeouts, idempotency, error taxonomy)
//   spec/protocol/RENDER-TRIGGER.md
//                               (CAP_RENDER, RENDER_TRIGGER 0x15 /
//                               NOISE_STREAM 0x16, the `rendering` state,
//                               the pass-1 declared-digest + sound-identity
//                               binding compared at the pass-2 trigger, and
//                               the bind_reject pulse that discards the
//                               clip in the replay engine)
//
// Word formats come from the accepted DR-0008 register through the emitted
// constants package (C4: the uniform host-entry patch word is Q10.21,
// stored and hashed verbatim, never interpreted); the name table, its
// pinned SHA-256 reference, the bound numeric-contract version, the
// patch-hash domain tag and the bound profile id come from the generated
// include gf180_patch_table.svh (tools/generate_patch_names.py, from the
// pinned inventory through its loader). The commit hash walks slots in
// slot order == sorted canonical-name byte order.
//
// The keyboard lane forms its two S1 outputs at commit-apply:
// keyboard.midi_f0 verbatim (the C4 wire word) and keyboard.duration as
// the exact widening (<<9) of the uniform wire word into the model's
// Q16.30 length domain (LENGTH_FORMAT, format_sweep.py:71). The wire word
// is uniform Q10.21 for every parameter, so the duration output is the
// exact widening of that word — not the model's direct binary64 -> Q16.30
// entry quantization (a declared wire-boundary artifact bounded by 2^-22).
//
// Declared reconciliations (each realizes one under-specified protocol
// sentence; the Python mirror src/torchsynth_voice/patch_control_model.py
// mirrors all of them and the shared test suite holds both sides to them):
//   1. The SESSION error table's per-error Recovery column governs: most
//      errors leave core state unchanged (an open transaction stays open);
//      ERR_PATCH_INCOMPLETE and ERR_PATCH_HASH_MISMATCH discard the
//      transaction and return to ready; only ERR_PROTOCOL_VERSION is
//      fatal (core -> closed, open-session state discarded in full).
//   2. A repeated PATCH_NAME for an already-declared name is the
//      idempotent no-op rewrite of SESSION (its payload is the name
//      itself, so a repeat is byte-identical by construction); only a
//      value re-staged with different bytes is ERR_DUPLICATE_NAME,
//      leaving the staged slot unchanged.
//   3. The idempotent-class replay caches (HELLO/RESET/PATCH_ABORT) hold
//      the most recent frame of each class keyed by sequence and exact
//      frame bytes; an identical re-received frame replays the cached
//      response and applies nothing. Bounded profile/source windows
//      (64 bytes, ERR_PAYLOAD_LENGTH overlong) keep every protocol-legal
//      frame of these classes within the cache bound.
//   4. A sub-patch (declared_name_count < 78) that commits cleanly
//      applies its words into the active bank but cannot assert the
//      render gate: patch_active asserts only when a hash-matching commit
//      applies a complete 78-name patch.
//   5. Transaction ids: re-opening a finished transaction's id answers
//      ERR_BAD_SEQUENCE; an expired one's id, or any transaction frame
//      replaying a sequence of an expired transaction, answers
//      ERR_TX_TIMEOUT; replaying a sequence of a finished transaction
//      answers ERR_BAD_SEQUENCE.
//   6. RESET in closed is ERR_BAD_STATE (the lifecycle table allows only
//      HELLO there); a payload that is not the shape its command defines
//      is ERR_PAYLOAD_LENGTH; a name the core cannot resolve — unknown,
//      overlong, or not valid UTF-8 — is unresolvable (ERR_UNKNOWN_NAME,
//      PATCH-LOAD's "must not accept names it cannot resolve").
//   7. Capabilities are negotiated, not asserted: the READY capability
//      word is (requested & CAPS_GRANTED), and a command whose capability
//      bit is not in that word is ERR_UNSUPPORTED_COMMAND — checked
//      BEFORE any state or payload rule, because RENDER-TRIGGER.md's
//      capability paragraph is unconditional ("A core that does not grant
//      it answers both codes with ERR_UNSUPPORTED_COMMAND and changes
//      nothing"). The negotiated word survives RESET (SESSION: RESET
//      returns to ready, it does not renegotiate) and is cleared only by
//      rst and by the two fatal ERR_PROTOCOL_VERSION paths, so `closed`
//      never holds a granted capability.
//   8. RENDER-TRIGGER.md's lifecycle table names `ready` and `rendering`
//      rows only. In `closed` the SESSION lifecycle admits HELLO alone, so
//      both render codes are ERR_BAD_STATE there (unreachable in practice
//      under refinement 7); in `patch_open` both are ERR_BAD_STATE (that
//      document's own ordering rule). While `rendering`: PATCH_OPEN and
//      PATCH_ABORT are ERR_BAD_STATE (an abort must not silently end a
//      render — RESET does that), and PATCH_NAME/PATCH_VALUE/
//      PATCH_COMMIT answer the no-open-transaction outcome they already
//      answer in `ready`. A rejection that discards an OPEN render
//      (ERR_RENDER_BINDING or ERR_NOISE_STREAM) pulses bind_reject for one
//      cycle on the decide cycle that answers it; a pass-1 identity
//      mismatch does not (no clip existed), and neither do RESET/HELLO,
//      whose discard reaches the engine through its own rst/abort.
//
// ---------------------------------------------------------------------
// CYCLE CONTRACT — replicated cycle-exactly by the Python mirror; the
// shared test suite asserts the full response byte stream against the
// mirror's prediction, so a timing change is a test failure, not drift:
//   - One command byte accepted per cycle; cmd_ready is constant 1.
//   - t_hdr (the 7th header byte) decides admission: unsupported header
//     version (fatal, -> closed) or unknown kind is answered there and
//     the frame's remaining bytes are consumed and discarded; a frame
//     whose header completes while the single frame slot is occupied is
//     answered ERR_BUSY there and discarded; a declared length above
//     MAX_PAYLOAD is a bad frame whose bytes are consumed. Resync after a
//     rejected frame is with the byte following the frame.
//   - t0 (the high CRC byte) completes an admitted frame; a CRC failure
//     answers ERR_BAD_FRAME (state unchanged; an open transaction stays
//     open). The engine takes a complete frame at the first later cycle
//     on which it is idle, no frame completes that cycle and the request
//     queue has room; it then walks a deterministic per-command schedule
//     (one payload byte consumed per walk cycle, plus the fixed 78-slot
//     name scan and the SHA-256 compress/compare phases) and enqueues its
//     response request on the walk's last (decide) cycle. Walk lengths:
//       RESET, PATCH_ABORT, unknown commands and all walk-free outcomes: 0.
//       HELLO: prof len [2], src len [2], contract prefix [2],
//                   contract [32], caps [2].
//       PATCH_OPEN: id len [2], id [id_len], identity len [2],
//                   identity [ident_len], table ref prefix [2],
//                   table ref [32], count [2].
//       PATCH_NAME: name len [2], name [nl], slot scan [78].
//       PATCH_VALUE: name len [2], name [nl], value len [2], value [vl],
//                   slot scan [78].
//       PATCH_COMMIT: the hash stream — 63 prefix bytes (domain tag,
//                   0x00, contract version), staged entries in slot order
//                   [sum(name_len+7)], padding [9+k_pad] bytes, one
//                   64-cycle compress per 64-byte block, a 32-cycle
//                   digest compare. k_pad = (56-((stream+1) mod 64)) mod 64.
//       RENDER_TRIGGER: pass index [1], identity len [2],
//                   identity [ident_len, 0 consumes no cycle], digest
//                   prefix [2], declared digest [32].
//       NOISE_STREAM: pass index [1], offset [4], data len [2]. The data
//                   region costs NO walk cycle: the bytes are consumed by
//                   the parser and never stored (DR-0010 Memory strategy —
//                   the receiver holds no clip buffer), so the engine only
//                   ever reads this frame's declared length.
//       Each walk is followed by one decide cycle (effects + enqueue).
//       A command whose capability is not granted, or whose payload is not
//       its declared shape (a length the walk itself could not traverse),
//       is answered walk-free at the decide cycle.
//   - Patch timeout: the timer runs only while the session is
//     patch_open, reloads on every admitted transaction frame's t0 (and
//     when a transaction opens), and expires silently after
//     TIMEOUT_CYCLES consecutive non-reloaded cycles (discard the staged
//     transaction, record it for ERR_TX_TIMEOUT continuation answers,
//     -> ready). TIMEOUT_CYCLES must exceed every command's walk length
//     so a commit can never expire mid-processing.
//   - Responses are emitted in request-enqueue order: the head request is
//     latched from the queue and serialized one byte per cycle while
//     rsp_ready (no flow control beyond the transport's own).
// ---------------------------------------------------------------------
//
// PDK-free plain SystemVerilog for Icarus Verilog 13 (-g2012): no
// interfaces, no classes, no vendor cells; the generated include carries
// the name ROM as simulation-time initial state. No PPA, fit, or
// synthesis-flow claim is made anywhere (DR-0010 P5 remains pending
// measurement); "synthesis-compatible" here means plain flip-flops, RAM
// arrays and one clock, nothing stronger.

`timescale 1ns/1ps

import gf180_rtl_constants::*;

module patch_control #(
    parameter integer MAX_PAYLOAD = 1024,
    parameter integer TIMEOUT_CYCLES = 5000,
    parameter integer ADVERTISED_MAX_PAYLOAD = 1024,
    parameter integer ADVERTISED_RX_QUEUE_DEPTH = 1,
    parameter integer ADVERTISED_PATCH_TIMEOUT_MS = 2,
    // Per-pass host-fed noise-stream length in bytes: the product
    // profile's clip length, SCHED_SAMPLES_PER_PASS x 4 = 705,600 B
    // (RENDER-TRIGGER.md "Render lifecycle"). A smaller value is a
    // simulation scale-down ONLY — it lets the directed framing/lifecycle
    // cases run in a few thousand cycles instead of ~1.4M; the product
    // length is this default, bound to the emitted schedule constant.
    parameter integer NOISE_CLIP_BYTES = SCHED_SAMPLES_PER_PASS * 4
) (
    input  wire        clk,
    input  wire        rst,          // synchronous, active-high

    input  wire        cmd_valid,
    input  wire [7:0]  cmd_byte,
    output wire        cmd_ready,

    output wire        rsp_valid,
    output wire [7:0]  rsp_byte,
    input  wire        rsp_ready,

    output wire [1:0]  state_out,    // 0 closed, 1 ready, 2 patch_open,
                                     // 3 rendering (RENDER-TRIGGER.md)
    output wire        patch_active, // render gate (refinement 4)
    // One-cycle pulse: this receiver answered ERR_RENDER_BINDING or
    // ERR_NOISE_STREAM for an OPEN render, so the clip is discarded
    // entire. Wired to normalization_replay_engine.bind_reject
    // (RENDER-TRIGGER.md "Where the binding reaches the RTL").
    output wire        bind_reject,
    output wire [C4_WIDTH-1:0] kbd_midi_f0_word, // C4 Q10.21, verbatim
    output wire [46:0] kbd_duration_word,  // Q16.30, exact wire-word <<9
    output wire [7:0]  sound_identity_len,
    output wire [511:0] sound_identity,
    input  wire [6:0]  probe_slot,
    output reg  [C4_WIDTH-1:0] probe_value,
    output wire        idle_out
);

`include "gf180_patch_table.svh"

    localparam [1:0] S_CLOSED = 2'd0, S_READY = 2'd1, S_PATCH = 2'd2,
        S_RENDER = 2'd3;

    localparam [7:0] CMD_HELLO = 8'h01, CMD_READY = 8'h02,
        CMD_PATCH_OPEN = 8'h10, CMD_PATCH_NAME = 8'h11,
        CMD_PATCH_VALUE = 8'h12, CMD_PATCH_COMMIT = 8'h13,
        CMD_PATCH_ABORT = 8'h14, CMD_RENDER_TRIGGER = 8'h15,
        CMD_NOISE_STREAM = 8'h16, CMD_RESET = 8'h20;

    localparam [7:0] KIND_CMD = 8'h01, KIND_RSP = 8'h02, KIND_ERR = 8'h03;

    localparam [7:0] ERR_UNSUPPORTED = 8'h01, ERR_VERSION = 8'h02,
        ERR_BAD_FRAME = 8'h03, ERR_BAD_SEQUENCE = 8'h04, ERR_BUSY = 8'h05,
        ERR_BAD_STATE = 8'h06, ERR_TX_ACTIVE = 8'h07, ERR_INCOMPLETE = 8'h08,
        ERR_HASH_MISMATCH = 8'h09, ERR_UNKNOWN_NAME = 8'h0A,
        ERR_DUPLICATE_NAME = 8'h0B, ERR_TX_TIMEOUT = 8'h0C,
        ERR_PAYLOAD_LENGTH = 8'h0D, ERR_RENDER_BINDING = 8'h0E,
        ERR_NOISE_STREAM = 8'h0F;

    localparam integer PROFILE_WINDOW = 64;
    localparam integer REQ_SLOTS  = 8;
    localparam integer CACHE_BYTES = 178; // bound idempotent frame (HELLO 64/64)
    // Capability bits (FRAMING.md / RENDER-TRIGGER.md "Capability").
    localparam [15:0] CAP_PATCH_LOAD = 16'h0001;
    localparam [15:0] CAP_RESET      = 16'h0002;
    localparam [15:0] CAP_RENDER     = 16'h0004;
    // name_keyed_patch_load | reset | render
    localparam [15:0] CAPS_GRANTED =
        CAP_PATCH_LOAD | CAP_RESET | CAP_RENDER;
    // The declared noise-stream digest width (RENDER_TRIGGER's opaque
    // 32-byte field) and the two render pass indices.
    localparam integer NOISE_DIGEST_BYTES = 32;
    localparam [7:0] RENDER_PASS_1 = 8'd1, RENDER_PASS_2 = 8'd2;
    // RENDER_TRIGGER's fixed payload cost: pass index [1] + identity
    // length prefix [2] + digest length prefix [2] + digest [32].
    localparam integer RENDER_TRIGGER_FIXED = 5 + NOISE_DIGEST_BYTES;
    // NOISE_STREAM's fixed payload cost: pass [1] + offset [4] + data
    // length prefix [2]; at least one data byte must follow.
    localparam integer NOISE_STREAM_FIXED = 7;

    // ------------------------------------------------------------------
    // state declarations (all before any use)
    // ------------------------------------------------------------------
    // byte-serial parser
    reg [2:0]  p_state;  // 0 hunt, 1 header, 2 payload, 3 crc_lo, 4 crc_hi, 5 skip
    reg [7:0]  p_prev;
    reg [7:0]  p_version, p_kind, p_command;
    reg [15:0] p_seq, p_len, p_pay, p_skip;
    reg [2:0]  p_cnt;
    reg [15:0] p_crc;
    reg [7:0]  p_crc_lo, p_crc_hi;
    reg        p_keep, p_bad_len;

    reg [7:0]  frame_ram [0:MAX_PAYLOAD-1];
    reg        frame_full;
    reg [7:0]  f_command;
    reg [15:0] f_seq, f_len;

    // session state
    reg [1:0]   session;
    reg [C4_WIDTH-1:0]  active_bank [0:NUM_PARAMS-1];
    reg [C4_WIDTH-1:0]  staged_bank [0:NUM_PARAMS-1];
    reg         patch_active_r;
    reg [C4_WIDTH-1:0] kbd_midi_r;
    reg [46:0]  kbd_dur_r;
    reg [7:0]   identity_len_r;
    reg [511:0] identity_r;

    reg [15:0]   tx_want, tx_have;
    reg [NUM_PARAMS-1:0] tx_declared_mask, tx_staged_mask;
    reg [127:0]  tx_id;
    reg [511:0]  tx_identity;
    reg [7:0]    tx_identity_len;
    reg [15:0]   tx_seq_first, tx_seq_last;
    reg [31:0]   patch_timer;

    reg          done_valid, done_expired;
    reg [127:0]  done_id;
    reg [15:0]   done_seq_first, done_seq_last;

    // Negotiated capability word (refinement 7): (requested & CAPS_GRANTED)
    // as of the last HELLO; 0 after rst and after a fatal.
    reg [15:0]   caps_r;

    // ------------------------------------------------------------------
    // render binding — the ONLY per-clip live state this receiver adds
    // (RENDER-TRIGGER.md "Clip lifecycle"; DR-0010 P2 live-state budget).
    // 256 + 512 + 8 + 1 + 1 + 32 = 810 bits, under DR-0010 P2's "~1 Kbit"
    // figure. No clip buffer exists: NOISE_STREAM data bytes are consumed
    // by the parser and never stored (DR-0010 Memory strategy), and this
    // receiver computes no digest of its own over them (declared-digest
    // binding only; receiver-side hashing is open, issue #207).
    // ------------------------------------------------------------------
    reg [255:0]  rnd_digest;        // pass-1 declared noise_stream_sha256
    reg [511:0]  rnd_identity;      // bound sound identity, left-justified
    reg [7:0]    rnd_identity_len;  // its declared length in bytes
    reg          rnd_pass2;         // 0 current pass is 1, 1 current is 2
    reg          rnd_pass1_done;    // pass 1 accepted NOISE_CLIP_BYTES
    reg [31:0]   rnd_accepted;      // bytes accepted in the current pass
    reg          bind_reject_r;     // one-cycle discard pulse (output)

    // idempotent-class replay caches (0 HELLO, 1 RESET, 2 PATCH_ABORT)
    reg [7:0]   cache_len [0:2];
    reg [15:0]  cache_seq [0:2];
    reg         cache_v [0:2];
    reg [7:0]   cache_bytes [0:2][0:CACHE_BYTES-1];

    // engine
    localparam [2:0] E_IDLE = 3'd0, E_WALK = 3'd1, E_DECIDE = 3'd2, E_CMP = 3'd3;

    reg [2:0]  e_state;
    reg [7:0]  e_cmd;
    reg [15:0] e_seq, e_len, e_ptr, e_skip;
    reg [5:0]  e_phase;
    reg [15:0] e_cnt;
    reg [7:0]  e_err;
    reg [15:0] e_flen_a, e_flen_b;

    reg [6:0]  lu_slot, lu_hit_slot;
    reg        lu_hit;
    reg [239:0] name_reg;
    reg [7:0]   name_len_r;
    reg [C4_WIDTH-1:0] v_word;

    reg [127:0] cap_id;
    reg [511:0] cap_b;
    reg [255:0] cap_c;
    reg [15:0]  cap_count;

    // commit stream controller
    reg [31:0] c_stream_len;  // message length before padding
    reg [31:0] c_kpad;
    reg [31:0] c_fed;
    reg [31:0] c_total;
    reg [6:0]  c_slot;
    reg [7:0]  c_entry_off;
    reg [7:0]  c_entry_len;
    reg        c_stream_done;
    reg        digest_mismatch;

    // SHA-256 (rolling 16-word message schedule)
    reg [7:0]  sha_blk [0:63];
    reg [6:0]  sha_fill;
    reg [31:0] sha_w [0:15];
    reg [31:0] sha_h [0:7];
    reg [31:0] sha_a, sha_b, sha_c, sha_d, sha_e, sha_f, sha_g, sha_hh;
    reg [5:0]  sha_round;
    reg        sha_busy;      // compressing a block
    reg [7:0]  sha_byte;

    // request queue + response builder + TX
    reg [7:0]  req_kind [0:REQ_SLOTS-1];
    reg [7:0]  req_cmd  [0:REQ_SLOTS-1];
    reg [15:0] req_seq  [0:REQ_SLOTS-1];
    reg [7:0]  req_plen [0:REQ_SLOTS-1];
    reg [7:0]  req_code [0:REQ_SLOTS-1];
    reg [15:0] req_caps [0:REQ_SLOTS-1];  // READY capability word snapshot
    reg [3:0]  req_wptr, req_rptr;

    reg        b_busy;
    reg [7:0]  b_index;
    reg [7:0]  b_kind, b_cmd, b_code;
    reg [15:0] b_seq, b_paylen;
    reg [15:0] b_caps;
    reg [15:0] b_crc;

    integer i;

    wire frame_completing = cmd_valid && (p_state == 3'd4) && p_keep &&
        (p_crc == {cmd_byte, p_crc_lo});

    // The declared payload length as of the t_hdr cycle: the high byte is
    // the byte arriving now, so p_len alone is one non-blocking assignment
    // behind and still carries the PREVIOUS frame's high byte.
    wire [15:0] hdr_len = {cmd_byte, p_len[7:0]};

    // ------------------------------------------------------------------
    // functions (pure: parameters and localparams only)
    // ------------------------------------------------------------------
    function [15:0] crc16_step(input [15:0] crc, input [7:0] d);
        integer k;
        reg [15:0] c;
        begin
            c = crc ^ (d << 8);
            for (k = 0; k < 8; k = k + 1)
                c = c[15] ? ((c << 1) ^ 16'h1021) : (c << 1);
            crc16_step = c[15:0];
        end
    endfunction

    function [31:0] sha_rotr(input [31:0] x, input [5:0] n);
        sha_rotr = (x >> n) | (x << (6'd32 - n));
    endfunction

    function [31:0] sha_k(input [5:0] i);
        begin
            case (i)
                6'd0:  sha_k = 32'h428a2f98;  6'd1:  sha_k = 32'h71374491;
                6'd2:  sha_k = 32'hb5c0fbcf;  6'd3:  sha_k = 32'he9b5dba5;
                6'd4:  sha_k = 32'h3956c25b;  6'd5:  sha_k = 32'h59f111f1;
                6'd6:  sha_k = 32'h923f82a4;  6'd7:  sha_k = 32'hab1c5ed5;
                6'd8:  sha_k = 32'hd807aa98;  6'd9:  sha_k = 32'h12835b01;
                6'd10: sha_k = 32'h243185be;  6'd11: sha_k = 32'h550c7dc3;
                6'd12: sha_k = 32'h72be5d74;  6'd13: sha_k = 32'h80deb1fe;
                6'd14: sha_k = 32'h9bdc06a7;  6'd15: sha_k = 32'hc19bf174;
                6'd16: sha_k = 32'he49b69c1;  6'd17: sha_k = 32'hefbe4786;
                6'd18: sha_k = 32'h0fc19dc6;  6'd19: sha_k = 32'h240ca1cc;
                6'd20: sha_k = 32'h2de92c6f;  6'd21: sha_k = 32'h4a7484aa;
                6'd22: sha_k = 32'h5cb0a9dc;  6'd23: sha_k = 32'h76f988da;
                6'd24: sha_k = 32'h983e5152;  6'd25: sha_k = 32'ha831c66d;
                6'd26: sha_k = 32'hb00327c8;  6'd27: sha_k = 32'hbf597fc7;
                6'd28: sha_k = 32'hc6e00bf3;  6'd29: sha_k = 32'hd5a79147;
                6'd30: sha_k = 32'h06ca6351;  6'd31: sha_k = 32'h14292967;
                6'd32: sha_k = 32'h27b70a85;  6'd33: sha_k = 32'h2e1b2138;
                6'd34: sha_k = 32'h4d2c6dfc;  6'd35: sha_k = 32'h53380d13;
                6'd36: sha_k = 32'h650a7354;  6'd37: sha_k = 32'h766a0abb;
                6'd38: sha_k = 32'h81c2c92e;  6'd39: sha_k = 32'h92722c85;
                6'd40: sha_k = 32'ha2bfe8a1;  6'd41: sha_k = 32'ha81a664b;
                6'd42: sha_k = 32'hc24b8b70;  6'd43: sha_k = 32'hc76c51a3;
                6'd44: sha_k = 32'hd192e819;  6'd45: sha_k = 32'hd6990624;
                6'd46: sha_k = 32'hf40e3585;  6'd47: sha_k = 32'h106aa070;
                6'd48: sha_k = 32'h19a4c116;  6'd49: sha_k = 32'h1e376c08;
                6'd50: sha_k = 32'h2748774c;  6'd51: sha_k = 32'h34b0bcb5;
                6'd52: sha_k = 32'h391c0cb3;  6'd53: sha_k = 32'h4ed8aa4a;
                6'd54: sha_k = 32'h5b9cca4f;  6'd55: sha_k = 32'h682e6ff3;
                6'd56: sha_k = 32'h748f82ee;  6'd57: sha_k = 32'h78a5636f;
                6'd58: sha_k = 32'h84c87814;  6'd59: sha_k = 32'h8cc70208;
                6'd60: sha_k = 32'h90befffa;  6'd61: sha_k = 32'ha4506ceb;
                6'd62: sha_k = 32'hbef9a3f7;  default: sha_k = 32'hc67178f2;
            endcase
        end
    endfunction

    // READY payload byte i (only serialized when plen == 71): the profile
    // and contract regions are opaque-packed (2-byte length prefix), the
    // locks region is empty — matching core_protocol.encode_ready exactly.
    // The capability word is the NEGOTIATED one this response snapshotted
    // at its enqueue (refinement 7), not the static CAPS_GRANTED mask.
    function [7:0] ready_byte(input [7:0] i);
        begin
            if (i == 0)                         ready_byte = PROFILE_ID_LEN[7:0];
            else if (i == 1)                    ready_byte = PROFILE_ID_LEN[15:8];
            else if (i < 2 + PROFILE_ID_LEN)
                ready_byte = PROFILE_ID[(26*8-1) - (i-2)*8 -: 8];
            else if (i == 28)                   ready_byte = 8'h20; // contract prefix
            else if (i == 29)                   ready_byte = 8'h00; // == 32
            else if (i < 62)
                ready_byte = NUMERIC_CONTRACT_VERSION[255 - (i-30)*8 -: 8];
            else if (i < 64)                    ready_byte = 8'h00; // locks: empty
            else if (i == 64)                   ready_byte = b_caps[7:0];
            else if (i == 65)                   ready_byte = b_caps[15:8];
            else if (i == 66)                   ready_byte = ADVERTISED_MAX_PAYLOAD[7:0];
            else if (i == 67)                   ready_byte = ADVERTISED_MAX_PAYLOAD[15:8];
            else if (i == 68)                   ready_byte = ADVERTISED_RX_QUEUE_DEPTH[7:0];
            else if (i == 69)                   ready_byte = ADVERTISED_PATCH_TIMEOUT_MS[7:0];
            else                                ready_byte = ADVERTISED_PATCH_TIMEOUT_MS[15:8];
        end
    endfunction

    // ------------------------------------------------------------------
    // tasks (may read/write module state)
    // ------------------------------------------------------------------
    // The name register is filled with nl left-to-right shifts, so the
    // name's first byte sits (NAME_BYTES - nl) bytes below the MSB;
    // shifting the left-justified ROM word by the same amount aligns the
    // two byte-for-byte (zero padding agrees on both sides).
    function [239:0] scan_candidate();
        begin
            scan_candidate = name_rom[lu_slot] >> ((NAME_BYTES - name_len_r) * 8);
        end
    endfunction
    // Reconstructed byte k of the frame currently in the engine (used by
    // the idempotency cache; the full frame including both CRC bytes).
    task frame_byte_of(input [15:0] k, output [7:0] b);
        begin
            if (k == 16'd0)      b = 8'h67;
            else if (k == 16'd1) b = 8'hF1;
            else if (k == 16'd2) b = 8'h02;
            else if (k == 16'd3) b = KIND_CMD;
            else if (k == 16'd4) b = e_cmd;
            else if (k == 16'd5) b = e_seq[7:0];
            else if (k == 16'd6) b = e_seq[15:8];
            else if (k == 16'd7) b = e_len[7:0];
            else if (k == 16'd8) b = e_len[15:8];
            else if (k < 9 + e_len) b = frame_ram[k - 9];
            else if (k == 9 + e_len) b = p_crc_lo;
            else                 b = p_crc_hi;
        end
    endtask

    // 1 iff the current frame matches the cached entry of its idempotent
    // class and sequence (a replay applies nothing).
    task cache_match(input [7:0] cmd, input [15:0] seq, output reg hit);
        integer k;
        reg [7:0] ci;
        reg [7:0] fb;
        begin
            hit = 1'b0;
            ci = (cmd == CMD_HELLO) ? 8'd0 : (cmd == CMD_RESET) ? 8'd1 : 8'd2;
            if (cache_v[ci] && (cache_seq[ci] == seq) &&
                (cache_len[ci] == 11 + e_len[7:0])) begin
                hit = 1'b1;
                for (k = 0; k < CACHE_BYTES; k = k + 1) begin
                    if (k < 11 + e_len) begin
                        frame_byte_of(k[15:0], fb);
                        if (cache_bytes[ci][k] != fb)
                            hit = 1'b0;
                    end
                end
            end
        end
    endtask

    task cache_store(input [7:0] cmd, input [15:0] seq);
        integer k;
        reg [7:0] ci;
        reg [7:0] fb;
        begin
            ci = (cmd == CMD_HELLO) ? 8'd0 : (cmd == CMD_RESET) ? 8'd1 : 8'd2;
            if (11 + e_len <= CACHE_BYTES) begin
                cache_v[ci] <= 1'b1;
                cache_seq[ci] <= seq;
                cache_len[ci] <= 11 + e_len[7:0];
                for (k = 0; k < CACHE_BYTES; k = k + 1) begin
                    if (k < 11 + e_len) begin
                        frame_byte_of(k[15:0], fb);
                        cache_bytes[ci][k] <= fb;
                    end
                end
            end
        end
    endtask

    // Discard the render binding whole (DR-0010: no resumable render).
    // Used by every "clip discarded entire" row and by clear_session.
    task discard_render;
        begin
            rnd_digest <= 256'h0;
            rnd_identity <= 512'h0;
            rnd_identity_len <= 8'h0;
            rnd_pass2 <= 1'b0;
            rnd_pass1_done <= 1'b0;
            rnd_accepted <= 32'd0;
        end
    endtask

    // discard all open-session state (RESET / renegotiating HELLO / fatal;
    // the applied patch is render state and is discarded with it)
    task clear_session(input [1:0] s);
        begin
            session <= s;
            discard_render;
            tx_want <= 16'd0; tx_have <= 16'd0;
            tx_declared_mask <= {NUM_PARAMS{1'b0}};
            tx_staged_mask <= {NUM_PARAMS{1'b0}};
            tx_seq_first <= 16'd0; tx_seq_last <= 16'd0;
            done_valid <= 1'b0; done_expired <= 1'b0;
            cache_v[0] <= 1'b0; cache_v[1] <= 1'b0; cache_v[2] <= 1'b0;
            patch_active_r <= 1'b0;
            kbd_midi_r <= {C4_WIDTH{1'b0}}; kbd_dur_r <= 47'h0;
            identity_len_r <= 8'h0; identity_r <= 512'h0;
            for (i = 0; i < NUM_PARAMS; i = i + 1) begin
                active_bank[i] <= {C4_WIDTH{1'b0}};
                staged_bank[i] <= {C4_WIDTH{1'b0}};
            end
            patch_timer <= TIMEOUT_CYCLES;
        end
    endtask

    task enq_rsp_err(input [7:0] cmd, input [15:0] seq, input [7:0] code);
        begin
            if ((req_wptr - req_rptr) < REQ_SLOTS) begin
                req_kind[req_wptr] <= KIND_ERR;
                req_cmd[req_wptr] <= cmd;
                req_seq[req_wptr] <= seq;
                req_plen[req_wptr] <= 8'd1;
                req_code[req_wptr] <= code;
                req_wptr <= (req_wptr == REQ_SLOTS-1) ? 4'd0 : req_wptr + 4'd1;
            end
        end
    endtask

    task enq_rsp(input [7:0] cmd, input [15:0] seq, input [7:0] plen);
        begin
            if ((req_wptr - req_rptr) < REQ_SLOTS) begin
                req_kind[req_wptr] <= KIND_RSP;
                req_cmd[req_wptr] <= cmd;
                req_seq[req_wptr] <= seq;
                req_plen[req_wptr] <= plen;
                req_code[req_wptr] <= 8'h00;
                req_wptr <= (req_wptr == REQ_SLOTS-1) ? 4'd0 : req_wptr + 4'd1;
            end
        end
    endtask

    // READY: the only response whose payload is not a pure function of
    // bound constants — it carries the negotiated capability word, so the
    // request snapshots it at enqueue (refinement 7).
    task enq_rsp_ready(input [7:0] cmd, input [15:0] seq,
                       input [15:0] caps);
        begin
            if ((req_wptr - req_rptr) < REQ_SLOTS) begin
                req_kind[req_wptr] <= KIND_RSP;
                req_cmd[req_wptr] <= cmd;
                req_seq[req_wptr] <= seq;
                req_plen[req_wptr] <= 8'd71;
                req_code[req_wptr] <= 8'h00;
                req_caps[req_wptr] <= caps;
                req_wptr <= (req_wptr == REQ_SLOTS-1) ? 4'd0 : req_wptr + 4'd1;
            end
        end
    endtask

    task finish_tx;
        begin
            done_id <= tx_id;
            done_seq_first <= tx_seq_first;
            done_seq_last <= tx_seq_last;
            done_valid <= 1'b1;
            done_expired <= 1'b0;
            tx_want <= 16'd0; tx_have <= 16'd0;
            tx_declared_mask <= {NUM_PARAMS{1'b0}};
            tx_staged_mask <= {NUM_PARAMS{1'b0}};
            patch_timer <= TIMEOUT_CYCLES;
        end
    endtask

    function [7:0] expired_or_bad_seq(input [15:0] seq);
        begin
            if (done_valid && done_expired &&
                (seq >= done_seq_first) && (seq <= done_seq_last))
                expired_or_bad_seq = ERR_TX_TIMEOUT;
            else
                expired_or_bad_seq = ERR_BAD_SEQUENCE;
        end
    endfunction

    // ------------------------------------------------------------------
    // parser
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (rst) begin
            p_state <= 3'd0; p_prev <= 8'h00; p_keep <= 1'b0;
            p_bad_len <= 1'b0; p_cnt <= 3'd0; p_pay <= 16'd0; p_skip <= 16'd0;
            p_crc <= 16'hFFFF; p_crc_lo <= 8'h00; p_crc_hi <= 8'h00;
            frame_full <= 1'b0;
        end else if (cmd_valid) begin
            case (p_state)
                3'd0: begin // HUNT
                    if ((p_prev == 8'h67) && (cmd_byte == 8'hF1)) begin
                        p_state <= 3'd1;
                        p_cnt <= 3'd0;
                        // CRC-16/CCITT-FALSE over everything after sync only
                        p_crc <= 16'hFFFF;
                        p_bad_len <= 1'b0;
                        p_keep <= 1'b0;
                    end
                    p_prev <= cmd_byte;
                end
                3'd1: begin // HEADER
                    p_crc <= crc16_step(p_crc, cmd_byte);
                    case (p_cnt)
                        3'd0: p_version <= cmd_byte;
                        3'd1: p_kind <= cmd_byte;
                        3'd2: p_command <= cmd_byte;
                        3'd3: p_seq[7:0] <= cmd_byte;
                        3'd4: p_seq[15:8] <= cmd_byte;
                        3'd5: p_len[7:0] <= cmd_byte;
                        default: p_len[15:8] <= cmd_byte;
                    endcase
                    if (p_cnt == 3'd6) begin // t_hdr admission decision
                        // hdr_len, not p_len: the high length byte IS this
                        // cycle's byte, and p_len does not carry it until
                        // the non-blocking assignment above commits. (A
                        // declared length whose low byte is 0 and high byte
                        // nonzero — any multiple of 256, e.g. the 1024-byte
                        // NOISE_STREAM payload this core advertises — was
                        // admitted as a zero-length frame before this was
                        // corrected, and the payload was reparsed as
                        // envelope bytes.)
                        if (p_version != 8'h02) begin
                            enq_rsp_err(p_command, p_seq, ERR_VERSION);
                            clear_session(S_CLOSED);
                            caps_r <= 16'd0;  // fatal: renegotiate (refinement 7)
                            p_state <= 3'd5;
                            p_skip <= hdr_len + 16'd2;
                        end else if (p_kind != KIND_CMD) begin
                            enq_rsp_err(p_command, p_seq, ERR_BAD_FRAME);
                            p_state <= 3'd5;
                            p_skip <= hdr_len + 16'd2;
                        end else if (hdr_len > MAX_PAYLOAD) begin
                            p_bad_len <= 1'b1;
                            p_state <= 3'd5;
                            p_skip <= hdr_len + 16'd2;
                        end else if (frame_full) begin
                            enq_rsp_err(p_command, p_seq, ERR_BUSY);
                            p_state <= 3'd5;
                            p_skip <= hdr_len + 16'd2;
                        end else begin
                            p_keep <= 1'b1;
                            p_pay <= 16'd0;
                            p_state <= (hdr_len == 16'd0) ? 3'd3 : 3'd2;
                        end
                    end
                    p_cnt <= p_cnt + 3'd1;
                end
                3'd2: begin // PAYLOAD
                    p_crc <= crc16_step(p_crc, cmd_byte);
                    if (p_keep)
                        frame_ram[p_pay] <= cmd_byte;
                    p_pay <= p_pay + 16'd1;
                    if (p_pay + 16'd1 == p_len)
                        p_state <= 3'd3;
                end
                3'd3: begin // CRC low
                    p_crc_lo <= cmd_byte;
                    p_state <= 3'd4;
                end
                3'd4: begin // CRC high = t0
                    p_crc_hi <= cmd_byte;
                    if (p_keep) begin
                        if (p_crc == {cmd_byte, p_crc_lo}) begin
                            frame_full <= 1'b1;
                            f_command <= p_command;
                            f_seq <= p_seq;
                            f_len <= p_len;
                            if ((session == S_PATCH) &&
                                (p_command >= CMD_PATCH_OPEN) &&
                                (p_command <= CMD_PATCH_ABORT))
                                patch_timer <= TIMEOUT_CYCLES;
                        end else begin
                            enq_rsp_err(p_command, p_seq, ERR_BAD_FRAME);
                        end
                    end
                    p_state <= 3'd0;
                    p_prev <= cmd_byte;
                end
                default: begin // SKIP the discarded bytes of a rejected frame
                    p_skip <= p_skip - 16'd1;
                    if (p_skip == 16'd1) begin
                        if (p_bad_len) begin
                            enq_rsp_err(p_command, p_seq, ERR_BAD_FRAME);
                            p_bad_len <= 1'b0;
                        end
                        p_state <= 3'd0;
                    end
                end
            endcase
        end
    end

    // ------------------------------------------------------------------
    // response builder + TX (second sequential writer; owns only the
    // builder/TX registers and req_rptr)
    // ------------------------------------------------------------------
    reg [7:0] ser_byte;
    always @(*) begin
        if (b_index <= 8'd8) begin
            case (b_index)
                8'd0: ser_byte = 8'h67;
                8'd1: ser_byte = 8'hF1;
                8'd2: ser_byte = 8'h02;  // protocol version
                8'd3: ser_byte = b_kind;
                8'd4: ser_byte = b_cmd;
                8'd5: ser_byte = b_seq[7:0];
                8'd6: ser_byte = b_seq[15:8];
                8'd7: ser_byte = b_paylen[7:0];
                default: ser_byte = b_paylen[15:8];
            endcase
        end else if (b_index < 9 + b_paylen[7:0]) begin
            ser_byte = (b_kind == KIND_ERR)
                ? b_code : ready_byte(b_index - 9);
        end else if (b_index == 9 + b_paylen[7:0]) begin
            ser_byte = b_crc[7:0];
        end else begin
            ser_byte = b_crc[15:8];
        end
    end

    // Direct serialization: the head request is latched and streamed one
    // byte per cycle while rsp_ready; responses are emitted strictly in
    // request-enqueue order (SESSION: no flow control beyond the
    // transport's own).
    always @(posedge clk) begin
        if (rst) begin
            b_busy <= 1'b0; b_index <= 8'd0; b_paylen <= 16'd0;
            b_crc <= 16'hFFFF;
            req_rptr <= 4'd0;
        end else begin
            if (!b_busy) begin
                if (req_wptr != req_rptr) begin
                    b_kind <= req_kind[req_rptr];
                    b_cmd <= req_cmd[req_rptr];
                    b_seq <= req_seq[req_rptr];
                    b_code <= req_code[req_rptr];
                    b_caps <= req_caps[req_rptr];
                    b_paylen <= (req_kind[req_rptr] == KIND_ERR)
                        ? 16'd1 : {8'd0, req_plen[req_rptr]};
                    b_crc <= 16'hFFFF;
                    b_busy <= 1'b1;
                    b_index <= 8'd0;
                    req_rptr <= (req_rptr == REQ_SLOTS-1)
                        ? 4'd0 : req_rptr + 4'd1;
                end
            end else if (rsp_ready) begin
                if ((b_index >= 8'd2) && (b_index < 9 + b_paylen[7:0]))
                    b_crc <= crc16_step(b_crc, ser_byte);
                b_index <= b_index + 8'd1;
                if (b_index == 10 + b_paylen[7:0])
                    b_busy <= 1'b0;
            end
        end
    end

    assign rsp_valid = b_busy;
    assign rsp_byte = ser_byte;
    assign cmd_ready = 1'b1;
    assign state_out = session;
    assign patch_active = patch_active_r;
    assign bind_reject = bind_reject_r;
    assign kbd_midi_f0_word = kbd_midi_r;
    assign kbd_duration_word = kbd_dur_r;
    assign sound_identity_len = identity_len_r;
    assign sound_identity = identity_r;
    assign idle_out = (e_state == E_IDLE) && !frame_full && !b_busy;

    // probe: active-bank word readout (AC 1 evidence surface)
    always @(*) probe_value = active_bank[probe_slot];

    // ------------------------------------------------------------------
    // SHA-256 round datapath (combinational over the current round)
    // ------------------------------------------------------------------
    wire [31:0] w_rol = sha_w[(sha_round[3:0] + 4'd1) & 4'hF];
    wire [31:0] w_9   = sha_w[(sha_round[3:0] + 4'd9) & 4'hF];
    wire [31:0] w_14  = sha_w[(sha_round[3:0] + 4'd14) & 4'hF];
    wire [31:0] w_ext = sha_w[sha_round[3:0]] +
        (sha_rotr(w_rol, 6'd7) ^ sha_rotr(w_rol, 6'd18) ^ (w_rol >> 3)) +
        w_9 +
        (sha_rotr(w_14, 6'd17) ^ sha_rotr(w_14, 6'd19) ^ (w_14 >> 10));
    wire [31:0] w_cur = (sha_round < 6'd16)
        ? {sha_blk[sha_round*4], sha_blk[sha_round*4+1],
           sha_blk[sha_round*4+2], sha_blk[sha_round*4+3]}  // big-endian
        : w_ext;

    // Round 0 works on the block's initial state (sha_h) directly; later
    // rounds work on the shifted working registers.
    wire [31:0] c_a = (sha_round == 6'd0) ? sha_h[0] : sha_a;
    wire [31:0] c_b = (sha_round == 6'd0) ? sha_h[1] : sha_b;
    wire [31:0] c_c = (sha_round == 6'd0) ? sha_h[2] : sha_c;
    wire [31:0] c_d = (sha_round == 6'd0) ? sha_h[3] : sha_d;
    wire [31:0] c_e = (sha_round == 6'd0) ? sha_h[4] : sha_e;
    wire [31:0] c_f = (sha_round == 6'd0) ? sha_h[5] : sha_f;
    wire [31:0] c_g = (sha_round == 6'd0) ? sha_h[6] : sha_g;
    wire [31:0] c_h = (sha_round == 6'd0) ? sha_h[7] : sha_hh;
    wire [31:0] r_s1 = sha_rotr(c_e, 6'd6) ^ sha_rotr(c_e, 6'd11) ^
                       sha_rotr(c_e, 6'd25);
    wire [31:0] r_ch = (c_e & c_f) ^ (~c_e & c_g);
    wire [31:0] r_t1 = c_h + r_s1 + r_ch + sha_k(sha_round) + w_cur;
    wire [31:0] r_s0 = sha_rotr(c_a, 6'd2) ^ sha_rotr(c_a, 6'd13) ^
                       sha_rotr(c_a, 6'd22);
    wire [31:0] r_maj = (c_a & c_b) ^ (c_a & c_c) ^ (c_b & c_c);
    wire [31:0] r_t2 = r_s0 + r_maj;

    // ------------------------------------------------------------------
    // master control block (single writer of all control state)
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (rst) begin
            e_state <= E_IDLE;
            session <= S_CLOSED;
            patch_active_r <= 1'b0;
            kbd_midi_r <= {C4_WIDTH{1'b0}}; kbd_dur_r <= 47'h0;
            identity_len_r <= 8'h0; identity_r <= 512'h0;
            tx_want <= 16'd0; tx_have <= 16'd0;
            tx_declared_mask <= {NUM_PARAMS{1'b0}};
            tx_staged_mask <= {NUM_PARAMS{1'b0}};
            tx_identity_len <= 8'h0;
            done_valid <= 1'b0; done_expired <= 1'b0;
            patch_timer <= TIMEOUT_CYCLES;
            cache_v[0] <= 1'b0; cache_v[1] <= 1'b0; cache_v[2] <= 1'b0;
            req_wptr <= 4'd0;
            sha_fill <= 7'd0; sha_busy <= 1'b0; sha_round <= 6'd0;
            sha_h[0] <= 32'h6a09e667; sha_h[1] <= 32'hbb67ae85;
            sha_h[2] <= 32'h3c6ef372; sha_h[3] <= 32'ha54ff53a;
            sha_h[4] <= 32'h510e527f; sha_h[5] <= 32'h9b05688c;
            sha_h[6] <= 32'h1f83d9ab; sha_h[7] <= 32'h5be0cd19;
            digest_mismatch <= 1'b0;
            e_phase <= 6'd0; e_cnt <= 16'd0; e_err <= 8'h00;
            for (i = 0; i < NUM_PARAMS; i = i + 1) begin
                active_bank[i] <= {C4_WIDTH{1'b0}};
                staged_bank[i] <= {C4_WIDTH{1'b0}};
            end
            c_slot <= 7'd0; c_entry_off <= 8'd0; c_entry_len <= 8'd0;
            c_stream_done <= 1'b0;
            c_stream_len <= 32'd0; c_kpad <= 32'd0; c_total <= 32'd0;
            c_fed <= 32'd0;
            caps_r <= 16'd0;
            rnd_digest <= 256'h0; rnd_identity <= 512'h0;
            rnd_identity_len <= 8'h0; rnd_pass2 <= 1'b0;
            rnd_pass1_done <= 1'b0; rnd_accepted <= 32'd0;
            bind_reject_r <= 1'b0;
        end else begin
            // bind_reject is a one-cycle pulse: the decide cycle that
            // answers a discarding rejection overrides this default.
            bind_reject_r <= 1'b0;

            // ----------------------------------------------------------
            // patch timeout (only while patch_open; reload at t0 handled
            // in the parser; expiry is silent per SESSION)
            // ----------------------------------------------------------
            if ((session == S_PATCH) && !frame_completing) begin
                if (patch_timer == 32'd1) begin
                    done_id <= tx_id;
                    done_seq_first <= tx_seq_first;
                    done_seq_last <= tx_seq_last;
                    done_valid <= 1'b1;
                    done_expired <= 1'b1;
                    tx_want <= 16'd0; tx_have <= 16'd0;
                    tx_declared_mask <= {NUM_PARAMS{1'b0}};
                    tx_staged_mask <= {NUM_PARAMS{1'b0}};
                    patch_timer <= TIMEOUT_CYCLES;
                    session <= S_READY;
                end else begin
                    patch_timer <= patch_timer - 32'd1;
                end
            end

            // ----------------------------------------------------------
            // engine
            // ----------------------------------------------------------
            case (e_state)
                E_IDLE: begin
                    if (frame_full && !frame_completing &&
                        ((req_wptr - req_rptr) < REQ_SLOTS)) begin
                        frame_full <= 1'b0;
                        e_cmd <= f_command;
                        e_seq <= f_seq;
                        e_len <= f_len;
                        e_err <= 8'h00;
                        e_ptr <= 16'd0;
                        e_phase <= 6'd0;
                        e_cnt <= 16'd0;
                        e_skip <= 16'd0;
                        lu_hit <= 1'b0;
                        name_reg <= 240'd0;
                        name_len_r <= 8'h0;
                        digest_mismatch <= 1'b0;
                        c_fed <= 32'd0;
                        c_stream_done <= 1'b0;
                        sha_fill <= 7'd0;
                        take_command(f_command);
                    end
                end

                E_WALK: begin
                    case (e_cmd)
                        CMD_HELLO: hello_walk;
                        CMD_PATCH_OPEN: open_walk;
                        CMD_PATCH_NAME: name_walk;
                        CMD_PATCH_VALUE: value_walk;
                        CMD_RENDER_TRIGGER: render_trigger_walk;
                        CMD_NOISE_STREAM: noise_stream_walk;
                        default: commit_walk;
                    endcase
                end

                E_CMP: begin
                    // digest byte i = sha_h[i/4] byte (3 - i%4), big-endian
                    if (((sha_h[e_cnt[4:2]] >> ((3 - e_cnt[1:0]) * 8)) & 8'hFF) !=
                        frame_ram[{1'b0, e_cnt[4:0]} + 16'd2])
                        digest_mismatch <= 1'b1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd31)
                        e_state <= E_DECIDE;
                end

                default: begin // E_DECIDE: effects + enqueue, then idle
                    decide_step;
                    e_state <= E_IDLE;
                end
            endcase

            // ----------------------------------------------------------
            // SHA-256 compressor (64 rounds per full block, 1/cycle)
            // ----------------------------------------------------------
            if (sha_busy) begin
                sha_w[sha_round[3:0]] <= w_cur;
                sha_a <= r_t1 + r_t2;
                sha_b <= c_a; sha_c <= c_b; sha_d <= c_c;
                sha_e <= c_d + r_t1; sha_f <= c_e; sha_g <= c_f;
                sha_hh <= c_g;
                if (sha_round == 6'd63) begin
                    sha_h[0] <= sha_h[0] + r_t1 + r_t2;
                    sha_h[1] <= sha_h[1] + c_a;
                    sha_h[2] <= sha_h[2] + c_b;
                    sha_h[3] <= sha_h[3] + c_c;
                    sha_h[4] <= sha_h[4] + c_d + r_t1;  // e carries +t1
                    sha_h[5] <= sha_h[5] + c_e;
                    sha_h[6] <= sha_h[6] + c_f;
                    sha_h[7] <= sha_h[7] + c_g;
                    sha_busy <= 1'b0;
                end
                sha_round <= sha_round + 6'd1;
            end
        end
    end

    // take: gating + walk-program selection (walk-free outcomes go
    // straight to decide)
    task take_command(input [7:0] cmd);
        reg [31:0] stream;
        reg [6:0]  slot;
        reg [15:0] ident_len;
        reg [15:0] data_len;
        integer guard;
        begin
            case (cmd)
                CMD_RESET, CMD_PATCH_ABORT: e_state <= E_DECIDE;
                CMD_HELLO: e_state <= E_WALK;
                CMD_PATCH_OPEN: begin
                    if (session != S_READY)
                        e_state <= E_DECIDE;  // TX_ACTIVE / BAD_STATE
                    else
                        e_state <= E_WALK;
                end
                CMD_PATCH_NAME: begin
                    if (session != S_PATCH)
                        e_state <= E_DECIDE;  // BAD_STATE / BAD_SEQUENCE
                    else
                        e_state <= E_WALK;
                end
                CMD_PATCH_VALUE: begin
                    if (session != S_PATCH)
                        e_state <= E_DECIDE;
                    else
                        e_state <= E_WALK;
                end
                CMD_PATCH_COMMIT: begin
                    if (session != S_PATCH)
                        e_state <= E_DECIDE;
                    else if (f_len != 16'd34) begin
                        e_err <= ERR_PAYLOAD_LENGTH;
                        e_state <= E_DECIDE;
                    end else if ((tx_have != tx_want) ||
                                 (tx_declared_mask == {NUM_PARAMS{1'b0}})) begin
                        e_state <= E_DECIDE;  // incomplete, pre-hash
                    end else begin
                        // commit stream setup (blocking; the feeder starts
                        // reading these next cycle)
                        stream = 63;
                        for (guard = 0; guard < NUM_PARAMS; guard = guard + 1)
                            if (tx_staged_mask[guard])
                                stream = stream + name_len_rom[guard[6:0]] + 7;
                        c_stream_len = stream;
                        c_kpad = (56 - ((stream + 1) % 64)) % 64;
                        c_total = stream + 9 + c_kpad;
                        slot = 0;
                        while ((slot < NUM_PARAMS) && !tx_staged_mask[slot])
                            slot = slot + 7'd1;
                        c_slot = slot;
                        c_entry_len = name_len_rom[slot] + 8'd7;
                        c_entry_off = 8'd0;
                        // each commit hashes from the SHA-256 IV
                        sha_h[0] <= 32'h6a09e667; sha_h[1] <= 32'hbb67ae85;
                        sha_h[2] <= 32'h3c6ef372; sha_h[3] <= 32'ha54ff53a;
                        sha_h[4] <= 32'h510e527f; sha_h[5] <= 32'h9b05688c;
                        sha_h[6] <= 32'h1f83d9ab; sha_h[7] <= 32'h5be0cd19;
                        e_state <= E_WALK;
                    end
                end
                CMD_RENDER_TRIGGER: begin
                    // Refinement 7: the capability gate is outermost.
                    if (!(caps_r & CAP_RENDER)) begin
                        e_err <= ERR_UNSUPPORTED;
                        e_state <= E_DECIDE;
                    end else if ((session == S_CLOSED) ||
                                 (session == S_PATCH)) begin
                        // refinement 8: decided at take, so the walk's
                        // field scratch is never read stale at decide
                        e_err <= ERR_BAD_STATE;
                        e_state <= E_DECIDE;
                    end else if (f_len < RENDER_TRIGGER_FIXED) begin
                        e_err <= ERR_PAYLOAD_LENGTH;
                        e_state <= E_DECIDE;
                    end else begin
                        ident_len = {frame_ram[2], frame_ram[1]};
                        if ((ident_len > PROFILE_WINDOW) ||
                            (f_len != RENDER_TRIGGER_FIXED + ident_len)) begin
                            e_err <= ERR_PAYLOAD_LENGTH;
                            e_state <= E_DECIDE;
                        end else begin
                            e_state <= E_WALK;
                        end
                    end
                end
                CMD_NOISE_STREAM: begin
                    if (!(caps_r & CAP_RENDER)) begin
                        e_err <= ERR_UNSUPPORTED;
                        e_state <= E_DECIDE;
                    end else if ((session == S_CLOSED) ||
                                 (session == S_PATCH)) begin
                        // refinement 8: decided at take, so the walk's
                        // field scratch is never read stale at decide
                        e_err <= ERR_BAD_STATE;
                        e_state <= E_DECIDE;
                    end else if (f_len < NOISE_STREAM_FIXED + 1) begin
                        e_err <= ERR_PAYLOAD_LENGTH;  // needs >= 1 data byte
                        e_state <= E_DECIDE;
                    end else begin
                        data_len = {frame_ram[6], frame_ram[5]};
                        if (f_len != NOISE_STREAM_FIXED + data_len) begin
                            e_err <= ERR_PAYLOAD_LENGTH;
                            e_state <= E_DECIDE;
                        end else begin
                            e_state <= E_WALK;
                        end
                    end
                end
                default: begin
                    e_err <= ERR_UNSUPPORTED;
                    e_state <= E_DECIDE;
                end
            endcase
        end
    endtask

    // ---------------- walk phases ----------------
    task hello_walk;
        begin
            case (e_phase)
                6'd0: begin // profile length (2 cycles)
                    if (e_cnt == 16'd0) begin
                        e_flen_a[7:0] <= frame_ram[0];
                    end else begin
                        e_flen_a[15:8] <= frame_ram[1];
                        if ({frame_ram[1], frame_ram[0]} > PROFILE_WINDOW)
                            e_err <= ERR_PAYLOAD_LENGTH;
                    end
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_phase <= 6'd1;
                        e_cnt <= 16'd0;
                        e_ptr <= 16'd2 + {8'd0, frame_ram[1], frame_ram[0]};
                    end
                end
                6'd1: begin // source length (2 cycles, region skipped)
                    if (e_cnt == 16'd0) begin
                        e_flen_b[7:0] <= frame_ram[e_ptr];
                    end else begin
                        e_flen_b[15:8] <= frame_ram[e_ptr];
                        if ({frame_ram[e_ptr], e_flen_b[7:0]} > PROFILE_WINDOW)
                            e_err <= ERR_PAYLOAD_LENGTH;
                    end
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_phase <= 6'd2;
                        e_cnt <= 16'd0;
                        e_ptr <= 16'd4 + {8'd0, frame_ram[1], frame_ram[0]} +
                                 {8'd0, frame_ram[e_ptr], e_flen_b[7:0]};
                    end
                end
                6'd2: begin // contract prefix (2 cycles, region skipped)
                    if (e_cnt == 16'd0) begin
                        e_flen_a[7:0] <= frame_ram[e_ptr];  // prefix lo
                    end
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_phase <= 6'd3;
                        e_cnt <= 16'd0;
                        // the contract region is opaque-packed: a 2-byte
                        // length prefix that must be exactly 32
                        if ({frame_ram[e_ptr], e_flen_a[7:0]} != 16'd32)
                            e_err <= ERR_PAYLOAD_LENGTH;
                    end
                end
                6'd3: begin // contract copy (32 cycles)
                    cap_c <= {cap_c[247:0], frame_ram[e_ptr]};
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd31) begin
                        e_phase <= 6'd4;
                        e_cnt <= 16'd0;
                    end
                end
                default: begin // caps (2 cycles)
                    if (e_cnt == 16'd0)
                        cap_count[7:0] <= frame_ram[e_ptr];
                    else
                        cap_count[15:8] <= frame_ram[e_ptr];
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_state <= E_DECIDE;
                        // exact-shape check: the payload is fully consumed
                        if ((e_ptr + 16'd1) != e_len)
                            e_err <= ERR_PAYLOAD_LENGTH;
                    end
                end
            endcase
        end
    endtask

    task open_walk;
        begin
            case (e_phase)
                6'd0: begin // tx id length (2)
                    if (e_cnt == 16'd0) begin
                        e_flen_a[7:0] <= frame_ram[0];
                    end else begin
                        e_flen_a[15:8] <= frame_ram[1];
                        if ((frame_ram[0] == 8'd0) ||
                            ({frame_ram[1], frame_ram[0]} > 16'd16))
                            e_err <= ERR_PAYLOAD_LENGTH; // 1..16 bytes
                        e_ptr <= 16'd2;
                    end
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_phase <= 6'd1;
                        e_cnt <= 16'd0;
                    end
                end
                6'd1: begin // tx id copy
                    cap_id <= {cap_id[119:0], frame_ram[e_ptr]};
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt + 16'd1 >= e_flen_a[7:0]) begin
                        e_phase <= 6'd2;
                        e_cnt <= 16'd0;
                    end
                end
                6'd2: begin // identity length (2)
                    if (e_cnt == 16'd0) begin
                        e_flen_b[7:0] <= frame_ram[e_ptr];
                    end else begin
                        e_flen_b[15:8] <= frame_ram[e_ptr];
                        if ({frame_ram[e_ptr], e_flen_b[7:0]} > PROFILE_WINDOW)
                            e_err <= ERR_PAYLOAD_LENGTH;
                    end
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_phase <= 6'd3;
                        e_cnt <= 16'd0;
                    end
                end
                6'd3: begin // identity copy (zero-length consumes nothing)
                    if (e_flen_b[7:0] != 8'd0) begin
                        cap_b <= {cap_b[503:0], frame_ram[e_ptr]};
                        e_ptr <= e_ptr + 16'd1;
                    end
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt + 16'd1 >= e_flen_b[7:0]) begin
                        e_phase <= 6'd4;
                        e_cnt <= 16'd0;
                    end
                end
                6'd4: begin // table reference prefix (2 cycles, opaque)
                    if (e_cnt == 16'd0) begin
                        e_flen_a[7:0] <= frame_ram[e_ptr];
                    end
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_phase <= 6'd5;
                        e_cnt <= 16'd0;
                        // the table reference is opaque-packed: its
                        // 2-byte length prefix must be exactly 32
                        if ({frame_ram[e_ptr], e_flen_a[7:0]} != 16'd32)
                            e_err <= ERR_PAYLOAD_LENGTH;
                    end
                end
                6'd5: begin // table reference copy (32)
                    cap_c <= {cap_c[247:0], frame_ram[e_ptr]};
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd31) begin
                        e_phase <= 6'd6;
                        e_cnt <= 16'd0;
                    end
                end
                default: begin // declared count (2)
                    if (e_cnt == 16'd0)
                        cap_count[7:0] <= frame_ram[e_ptr];
                    else
                        cap_count[15:8] <= frame_ram[e_ptr];
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_state <= E_DECIDE;
                        if ((e_ptr + 16'd1) != e_len)
                            e_err <= ERR_PAYLOAD_LENGTH;
                    end
                end
            endcase
        end
    endtask

    task name_walk;
        begin
            case (e_phase)
                6'd0: begin // name length (2)
                    if (e_cnt == 16'd0) begin
                        e_flen_a[7:0] <= frame_ram[0];
                        name_len_r <= frame_ram[0];  // canonical names <= 30
                    end else begin
                        e_flen_a[15:8] <= frame_ram[1];
                        e_skip <= {frame_ram[1], frame_ram[0]};
                        e_ptr <= 16'd2;
                    end
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_phase <= 6'd1;
                        e_cnt <= 16'd0;
                    end
                end
                6'd1: begin // name copy (first NAME_BYTES, skip the rest)
                    if ((e_cnt < e_skip) && (e_cnt < NAME_BYTES))
                        name_reg <= {name_reg[231:0], frame_ram[e_ptr]};
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt + 16'd1 >= e_skip) begin
                        e_phase <= 6'd2;
                        e_cnt <= 16'd0;
                        lu_slot <= 7'd0;
                        lu_hit <= 1'b0;
                        if (e_ptr + 16'd1 != e_len)
                            e_err <= ERR_PAYLOAD_LENGTH;
                    end
                end
                default: begin // slot scan (78)
                    if ((scan_candidate() == name_reg) &&
                        (name_len_rom[lu_slot] == name_len_r)) begin
                        lu_hit <= 1'b1;
                        lu_hit_slot <= lu_slot;
                    end
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd77)
                        e_state <= E_DECIDE;
                    else
                        lu_slot <= lu_slot + 7'd1;
                end
            endcase
        end
    endtask

    task value_walk;
        begin
            case (e_phase)
                6'd0: begin // name length (2)
                    if (e_cnt == 16'd0) begin
                        e_flen_a[7:0] <= frame_ram[0];
                        name_len_r <= frame_ram[0];  // canonical names <= 30
                    end else begin
                        e_flen_a[15:8] <= frame_ram[1];
                        e_skip <= {frame_ram[1], frame_ram[0]};
                        e_ptr <= 16'd2;
                    end
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_phase <= 6'd1;
                        e_cnt <= 16'd0;
                    end
                end
                6'd1: begin // name copy
                    if ((e_cnt < e_skip) && (e_cnt < NAME_BYTES))
                        name_reg <= {name_reg[231:0], frame_ram[e_ptr]};
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt + 16'd1 >= e_skip) begin
                        e_phase <= 6'd2;
                        e_cnt <= 16'd0;
                    end
                end
                6'd2: begin // value length (2)
                    if (e_cnt == 16'd0) begin
                        e_flen_b[7:0] <= frame_ram[e_ptr];
                    end else begin
                        e_flen_b[15:8] <= frame_ram[e_ptr];
                        e_skip <= {frame_ram[e_ptr], e_flen_b[7:0]}; // value len
                    end
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_phase <= 6'd3;
                        e_cnt <= 16'd0;
                    end
                end
                6'd3: begin // value copy (4-byte values are the only legal shape)
                    if (e_cnt == 16'd0) v_word[7:0] <= frame_ram[e_ptr];
                    if (e_cnt == 16'd1) v_word[15:8] <= frame_ram[e_ptr];
                    if (e_cnt == 16'd2) v_word[23:16] <= frame_ram[e_ptr];
                    if (e_cnt == 16'd3) v_word[31:24] <= frame_ram[e_ptr];
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt + 16'd1 >= e_skip) begin
                        e_phase <= 6'd4;
                        e_cnt <= 16'd0;
                        lu_slot <= 7'd0;
                        lu_hit <= 1'b0;
                        if (e_ptr + 16'd1 != e_len)
                            e_err <= ERR_PAYLOAD_LENGTH;
                    end
                end
                default: begin // slot scan (78)
                    if ((scan_candidate() == name_reg) &&
                        (name_len_rom[lu_slot] == name_len_r)) begin
                        lu_hit <= 1'b1;
                        lu_hit_slot <= lu_slot;
                    end
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd77)
                        e_state <= E_DECIDE;
                    else
                        lu_slot <= lu_slot + 7'd1;
                end
            endcase
        end
    endtask

    // RENDER_TRIGGER: pass index, the bound sound identity and the
    // transport-declared 32-byte noise-stream digest. No digest is
    // computed here — the declared value is latched (pass 1) or compared
    // against the latch (pass 2), RENDER-TRIGGER.md "What is bound".
    task render_trigger_walk;
        begin
            case (e_phase)
                6'd0: begin // pass index (1 cycle)
                    cap_count <= {8'd0, frame_ram[0]};
                    if ((frame_ram[0] != RENDER_PASS_1) &&
                        (frame_ram[0] != RENDER_PASS_2))
                        e_err <= ERR_PAYLOAD_LENGTH;
                    e_phase <= 6'd1;
                    e_cnt <= 16'd0;
                end
                6'd1: begin // identity length (2 cycles)
                    if (e_cnt == 16'd0) begin
                        e_flen_b[7:0] <= frame_ram[1];
                    end else begin
                        e_flen_b[15:8] <= frame_ram[2];
                    end
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_cnt <= 16'd0;
                        e_ptr <= 16'd3;
                        // a zero-length identity consumes no walk cycle
                        e_phase <= ({frame_ram[2], frame_ram[1]} == 16'd0)
                            ? 6'd3 : 6'd2;
                    end
                end
                6'd2: begin // identity copy (ident_len cycles)
                    cap_b <= {cap_b[503:0], frame_ram[e_ptr]};
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt + 16'd1 >= e_flen_b) begin
                        e_phase <= 6'd3;
                        e_cnt <= 16'd0;
                    end
                end
                6'd3: begin // digest length prefix (2 cycles, opaque-packed)
                    if (e_cnt == 16'd0) begin
                        e_flen_a[7:0] <= frame_ram[e_ptr];
                    end
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1) begin
                        e_phase <= 6'd4;
                        e_cnt <= 16'd0;
                        // the digest region is opaque-packed: its 2-byte
                        // length prefix must be exactly 32
                        if ({frame_ram[e_ptr], e_flen_a[7:0]} != 16'd32)
                            e_err <= ERR_PAYLOAD_LENGTH;
                    end
                end
                default: begin // declared digest copy (32 cycles)
                    cap_c <= {cap_c[247:0], frame_ram[e_ptr]};
                    e_ptr <= e_ptr + 16'd1;
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd31)
                        e_state <= E_DECIDE;
                end
            endcase
        end
    endtask

    // NOISE_STREAM: pass index, offset and the declared data length. The
    // data region itself is NOT walked — the bytes are consumed by the
    // parser and never stored (DR-0010 Memory strategy: the receiver
    // retains no clip buffer), so only this frame's length matters.
    task noise_stream_walk;
        begin
            case (e_phase)
                6'd0: begin // pass index (1 cycle)
                    e_flen_a <= {8'd0, frame_ram[0]};
                    if ((frame_ram[0] != RENDER_PASS_1) &&
                        (frame_ram[0] != RENDER_PASS_2))
                        e_err <= ERR_PAYLOAD_LENGTH;
                    e_phase <= 6'd1;
                    e_cnt <= 16'd0;
                end
                6'd1: begin // offset, u32 little-endian (4 cycles)
                    case (e_cnt[1:0])
                        2'd0: cap_id[7:0]   <= frame_ram[1];
                        2'd1: cap_id[15:8]  <= frame_ram[2];
                        2'd2: cap_id[23:16] <= frame_ram[3];
                        default: cap_id[31:24] <= frame_ram[4];
                    endcase
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd3) begin
                        e_phase <= 6'd2;
                        e_cnt <= 16'd0;
                    end
                end
                default: begin // data length prefix (2 cycles)
                    if (e_cnt == 16'd0)
                        cap_count[7:0] <= frame_ram[5];
                    else
                        cap_count[15:8] <= frame_ram[6];
                    e_cnt <= e_cnt + 16'd1;
                    if (e_cnt == 16'd1)
                        e_state <= E_DECIDE;
                end
            endcase
        end
    endtask

    // PATCH_COMMIT: stream the domain-separated hash message into the
    // SHA-256 engine (the feeder stalls while a block compresses), then
    // compare the digest against the declared hash.
    task commit_walk;
        begin
            if (!c_stream_done) begin
                if (!sha_busy) begin
                    commit_next_byte(sha_byte);
                    sha_blk[sha_fill] <= sha_byte;
                    c_fed <= c_fed + 32'd1;
                    if (sha_fill == 7'd63) begin
                        sha_fill <= 7'd0;
                        sha_busy <= 1'b1;
                        sha_round <= 6'd0;
                    end else begin
                        sha_fill <= sha_fill + 7'd1;
                    end
                    if (c_fed + 32'd1 == c_total)
                        c_stream_done <= 1'b1;
                end
            end else if (!sha_busy) begin
                e_state <= E_CMP;
                e_cnt <= 16'd0;
            end
        end
    endtask

    // The commit stream: 63 prefix bytes (domain tag, 0x00, contract),
    // staged entries in slot order (name + 0x00 + value LE + u16 length),
    // then 0x80 + zero pad + 64-bit big-endian bit length.
    task commit_next_byte(output [7:0] b);
        reg [63:0] bitlen;
        reg [31:0] off;
        begin
            if (c_fed < DOMAIN_TAG_LEN) begin
                b = PATCH_DOMAIN_TAG[(DOMAIN_TAG_LEN*8-1) - c_fed*8 -: 8];
            end else if (c_fed < DOMAIN_TAG_LEN + 32) begin
                b = NUMERIC_CONTRACT_VERSION[255 - (c_fed-DOMAIN_TAG_LEN)*8 -: 8];
            end else if (c_fed < c_stream_len) begin
                commit_entry_byte(b);
            end else begin
                off = c_fed - c_stream_len;  // padding region
                if (off == 32'd0)
                    b = 8'h80;
                else if (off < 1 + c_kpad)
                    b = 8'h00;
                else begin
                    bitlen = {32'd0, c_stream_len} << 3;  // bits
                    b = bitlen >> (8*(7 - (off - 1 - c_kpad)));
                end
            end
        end
    endtask

    task commit_entry_byte(output [7:0] b);
        reg [7:0] nl;
        integer guard;
        begin
            nl = name_len_rom[c_slot];
            if (c_entry_off < nl)
                b = name_rom[c_slot] >> ((NAME_BYTES - 1 - c_entry_off) * 8);
            else if (c_entry_off == nl)
                b = 8'h00;  // separator
            else if (c_entry_off < nl + 8'd5)
                b = staged_bank[c_slot] >> ((c_entry_off - nl - 8'd1) * 8);
            else
                b = 8'd4 >> ((c_entry_off - nl - 8'd5) * 8);  // u16 LE length
            c_entry_off = c_entry_off + 8'd1;
            if (c_entry_off == c_entry_len) begin
                // advance to the next staged slot (blocking: same-cycle
                // state for the next entry region)
                c_slot = c_slot + 7'd1;
                guard = 0;
                while ((guard < NUM_PARAMS) && !tx_staged_mask[c_slot]) begin
                    c_slot = c_slot + 7'd1;
                    guard = guard + 1;
                end
                c_entry_len = name_len_rom[c_slot] + 8'd7;
                c_entry_off = 8'd0;
            end
        end
    endtask

    // ---------------- decide: effects + enqueue ----------------
    task decide_step;
        reg hit;
        begin
            case (e_cmd)
                CMD_RESET: begin
                    cache_match(e_cmd, e_seq, hit);
                    if (session == S_CLOSED)
                        enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
                    else if (hit)
                        enq_rsp(e_cmd, e_seq, 8'd0);
                    else begin
                        clear_session(S_READY);
                        cache_store(e_cmd, e_seq);
                        enq_rsp(e_cmd, e_seq, 8'd0);
                    end
                end
                CMD_PATCH_ABORT: begin
                    cache_match(e_cmd, e_seq, hit);
                    if (session == S_CLOSED)
                        enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
                    else if (session == S_RENDER)
                        // refinement 8: an abort must not silently end a
                        // render; RESET does that.
                        enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
                    else if (session == S_READY)
                        enq_rsp_err(e_cmd, e_seq, expired_or_bad_seq(e_seq));
                    else if (hit)
                        enq_rsp(e_cmd, e_seq, 8'd0);
                    else begin
                        finish_tx;
                        session <= S_READY;
                        cache_store(e_cmd, e_seq);
                        enq_rsp(e_cmd, e_seq, 8'd0);
                    end
                end
                CMD_HELLO: decide_hello;
                CMD_PATCH_OPEN: decide_open;
                CMD_PATCH_NAME: decide_name;
                CMD_PATCH_VALUE: decide_value;
                CMD_PATCH_COMMIT: decide_commit;
                CMD_RENDER_TRIGGER: decide_render_trigger;
                CMD_NOISE_STREAM: decide_noise_stream;
                default: enq_rsp_err(e_cmd, e_seq, ERR_UNSUPPORTED);
            endcase
        end
    endtask

    task decide_hello;
        reg hit;
        begin
            cache_match(e_cmd, e_seq, hit);
            if (e_err == ERR_PAYLOAD_LENGTH)
                enq_rsp_err(e_cmd, e_seq, ERR_PAYLOAD_LENGTH);
            else if (hit) begin
                // an identical replay carries identical caps by construction
                caps_r <= cap_count & CAPS_GRANTED;
                enq_rsp_ready(e_cmd, e_seq, cap_count & CAPS_GRANTED);
            end else if (cap_c != NUMERIC_CONTRACT_VERSION) begin
                clear_session(S_CLOSED);  // fatal (refinement 1)
                caps_r <= 16'd0;          // renegotiate (refinement 7)
                enq_rsp_err(e_cmd, e_seq, ERR_VERSION);
            end else begin
                clear_session(S_READY);
                caps_r <= cap_count & CAPS_GRANTED;  // negotiated
                cache_store(e_cmd, e_seq);
                enq_rsp_ready(e_cmd, e_seq, cap_count & CAPS_GRANTED);
            end
        end
    endtask

    task decide_open;
        begin
            if (session == S_PATCH)
                enq_rsp_err(e_cmd, e_seq, ERR_TX_ACTIVE);
            else if (session == S_CLOSED)
                enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
            else if (session == S_RENDER)
                // refinement 8 / RENDER-TRIGGER.md ordering rule: render
                // and patch-transaction frames never interleave.
                enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
            else if (e_err == ERR_PAYLOAD_LENGTH)
                enq_rsp_err(e_cmd, e_seq, ERR_PAYLOAD_LENGTH);
            else if ((cap_count < 16'd1) || (cap_count > NUM_PARAMS))
                enq_rsp_err(e_cmd, e_seq, ERR_PAYLOAD_LENGTH);
            else if (cap_c != NAME_TABLE_SHA256)
                enq_rsp_err(e_cmd, e_seq, ERR_UNKNOWN_NAME);
            else if (done_valid && (done_id == cap_id))
                enq_rsp_err(e_cmd, e_seq,
                    done_expired ? ERR_TX_TIMEOUT : ERR_BAD_SEQUENCE);
            else begin
                tx_want <= cap_count;
                tx_have <= 16'd0;
                tx_declared_mask <= {NUM_PARAMS{1'b0}};
                tx_staged_mask <= {NUM_PARAMS{1'b0}};
                tx_id <= cap_id;
                tx_identity <= cap_b;
                tx_identity_len <= e_flen_b[7:0];
                tx_seq_first <= e_seq;
                tx_seq_last <= e_seq;
                patch_timer <= TIMEOUT_CYCLES;
                session <= S_PATCH;
                enq_rsp(e_cmd, e_seq, 8'd0);
            end
        end
    endtask

    task decide_name;
        begin
            if (session == S_CLOSED)
                enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
            else if (session != S_PATCH)
                // ready or rendering: no transaction is open (refinement 8)
                enq_rsp_err(e_cmd, e_seq, expired_or_bad_seq(e_seq));
            else begin
                tx_seq_last <= e_seq;
                if (e_err == ERR_PAYLOAD_LENGTH)
                    enq_rsp_err(e_cmd, e_seq, ERR_PAYLOAD_LENGTH);
                else if (!lu_hit)
                    enq_rsp_err(e_cmd, e_seq, ERR_UNKNOWN_NAME);
                else if (tx_declared_mask[lu_hit_slot])
                    enq_rsp(e_cmd, e_seq, 8'd0);  // no-op re-declaration
                else begin
                    tx_declared_mask[lu_hit_slot] <= 1'b1;
                    tx_have <= tx_have + 16'd1;
                    enq_rsp(e_cmd, e_seq, 8'd0);
                end
            end
        end
    endtask

    task decide_value;
        begin
            if (session == S_CLOSED)
                enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
            else if (session != S_PATCH)
                // ready or rendering: no transaction is open (refinement 8)
                enq_rsp_err(e_cmd, e_seq, expired_or_bad_seq(e_seq));
            else begin
                tx_seq_last <= e_seq;
                if (e_err == ERR_PAYLOAD_LENGTH)
                    enq_rsp_err(e_cmd, e_seq, ERR_PAYLOAD_LENGTH);
                else if (e_flen_b != 16'd4)
                    enq_rsp_err(e_cmd, e_seq, ERR_PAYLOAD_LENGTH);
                else if (!lu_hit)
                    enq_rsp_err(e_cmd, e_seq, ERR_UNKNOWN_NAME);
                else if (!tx_declared_mask[lu_hit_slot])
                    enq_rsp_err(e_cmd, e_seq, ERR_UNKNOWN_NAME);
                else if (tx_staged_mask[lu_hit_slot] &&
                         (staged_bank[lu_hit_slot] != v_word))
                    enq_rsp_err(e_cmd, e_seq, ERR_DUPLICATE_NAME);
                else begin
                    tx_staged_mask[lu_hit_slot] <= 1'b1;
                    staged_bank[lu_hit_slot] <= v_word;
                    enq_rsp(e_cmd, e_seq, 8'd0);
                end
            end
        end
    endtask

    task decide_commit;
        begin
            if (session == S_CLOSED)
                enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
            else if (session != S_PATCH)
                // ready or rendering: no transaction is open (refinement 8)
                enq_rsp_err(e_cmd, e_seq, expired_or_bad_seq(e_seq));
            else if ((e_len != 16'd34) ||
                     ({frame_ram[1], frame_ram[0]} != 16'd32))
                enq_rsp_err(e_cmd, e_seq, ERR_PAYLOAD_LENGTH);
            else if ((tx_have != tx_want) ||
                     (tx_declared_mask == {NUM_PARAMS{1'b0}})) begin
                finish_tx;
                session <= S_READY;
                enq_rsp_err(e_cmd, e_seq, ERR_INCOMPLETE);
            end else if (digest_mismatch) begin
                finish_tx;
                session <= S_READY;
                enq_rsp_err(e_cmd, e_seq, ERR_HASH_MISMATCH);
            end else begin
                // atomic apply (refinement 4)
                for (i = 0; i < NUM_PARAMS; i = i + 1) begin
                    if (tx_staged_mask[i])
                        active_bank[i] <= staged_bank[i];
                end
                if (tx_want == NUM_PARAMS)
                    patch_active_r <= 1'b1;
                if (tx_staged_mask[KBD_MIDI_SLOT])
                    kbd_midi_r <= staged_bank[KBD_MIDI_SLOT];
                if (tx_staged_mask[KBD_DUR_SLOT])
                    kbd_dur_r <= {staged_bank[KBD_DUR_SLOT][31:0], 9'd0};  // Q10.21 <<9 -> Q16.30
                identity_r <= tx_identity << (8*(64 - tx_identity_len));
                identity_len_r <= tx_identity_len;
                finish_tx;
                session <= S_READY;
                enq_rsp(e_cmd, e_seq, 8'd0);
            end
        end
    endtask

    // ---------------- render lane (RENDER-TRIGGER.md) ----------------
    // The single site that drives the engine's bind_reject: a rejection
    // answered for an OPEN render. One cycle, sticky nowhere here — the
    // engine's own ERR_BINDING_REJECTED is what stays sticky until rst.
    task pulse_bind_reject;
        begin
            bind_reject_r <= 1'b1;
        end
    endtask

    localparam [31:0] CLIP_BYTES_W = NOISE_CLIP_BYTES;

    task decide_render_trigger;
        reg [511:0] ident;
        begin
            // cap_b holds the trigger's identity right-justified; the same
            // left-justifying shift decide_commit applies to the active
            // patch's identity makes the two comparable byte-for-byte.
            ident = cap_b << (8*(64 - e_flen_b[7:0]));
            if (e_err == ERR_UNSUPPORTED)
                enq_rsp_err(e_cmd, e_seq, ERR_UNSUPPORTED);
            else if (e_err == ERR_BAD_STATE)
                enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
            else if (e_err == ERR_PAYLOAD_LENGTH)
                enq_rsp_err(e_cmd, e_seq, ERR_PAYLOAD_LENGTH);
            else if (cap_count[7:0] == RENDER_PASS_1) begin
                if (session == S_RENDER)
                    // a second trigger never joins or replaces a clip
                    enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
                else if (!patch_active_r)
                    enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
                else if ((e_flen_b[7:0] != identity_len_r) ||
                         (ident != identity_r))
                    // no clip existed, so nothing is discarded and
                    // bind_reject stays low
                    enq_rsp_err(e_cmd, e_seq, ERR_RENDER_BINDING);
                else begin
                    // bind (sound_identity, noise_stream_sha256) for the
                    // whole clip; current pass 1, 0 bytes accepted
                    rnd_digest <= cap_c;
                    rnd_identity <= ident;
                    rnd_identity_len <= e_flen_b[7:0];
                    rnd_pass2 <= 1'b0;
                    rnd_pass1_done <= 1'b0;
                    rnd_accepted <= 32'd0;
                    session <= S_RENDER;
                    enq_rsp(e_cmd, e_seq, 8'd0);
                end
            end else begin  // pass 2
                if (session != S_RENDER)
                    enq_rsp_err(e_cmd, e_seq, ERR_BAD_SEQUENCE);
                else if (rnd_pass2 || !rnd_pass1_done) begin
                    // pass 1 short of the clip length, or pass 2 already
                    // open: clip discarded entire
                    discard_render;
                    session <= S_READY;
                    pulse_bind_reject;
                    enq_rsp_err(e_cmd, e_seq, ERR_NOISE_STREAM);
                end else if ((e_flen_b[7:0] != rnd_identity_len) ||
                             (ident != rnd_identity) ||
                             (cap_c != rnd_digest)) begin
                    // the DR-0010 binding check, BEFORE any pass-2 noise
                    // byte exists: zero samples can have been released
                    discard_render;
                    session <= S_READY;
                    pulse_bind_reject;
                    enq_rsp_err(e_cmd, e_seq, ERR_RENDER_BINDING);
                end else begin
                    rnd_pass2 <= 1'b1;
                    rnd_accepted <= 32'd0;
                    enq_rsp(e_cmd, e_seq, 8'd0);
                end
            end
        end
    endtask

    task decide_noise_stream;
        reg [31:0] dlen;
        reg [31:0] noff;
        reg [7:0]  want_pass;
        reg        fault;
        begin
            dlen = {16'd0, cap_count};
            noff = cap_id[31:0];
            want_pass = rnd_pass2 ? RENDER_PASS_2 : RENDER_PASS_1;
            if (e_err == ERR_UNSUPPORTED)
                enq_rsp_err(e_cmd, e_seq, ERR_UNSUPPORTED);
            else if (e_err == ERR_BAD_STATE)
                enq_rsp_err(e_cmd, e_seq, ERR_BAD_STATE);
            else if (e_err == ERR_PAYLOAD_LENGTH)
                enq_rsp_err(e_cmd, e_seq, ERR_PAYLOAD_LENGTH);
            else if (session != S_RENDER)
                enq_rsp_err(e_cmd, e_seq, ERR_BAD_SEQUENCE);
            else begin
                fault = (e_flen_a[7:0] != want_pass) ||
                        (noff != rnd_accepted) ||
                        (!rnd_pass2 && rnd_pass1_done) ||
                        ((rnd_accepted + dlen) > CLIP_BYTES_W);
                if (fault) begin
                    discard_render;
                    session <= S_READY;
                    pulse_bind_reject;
                    enq_rsp_err(e_cmd, e_seq, ERR_NOISE_STREAM);
                end else begin
                    // the bytes are consumed as they arrive; only the
                    // accepted count is kept (no clip buffer)
                    rnd_accepted <= rnd_accepted + dlen;
                    if ((rnd_accepted + dlen) == CLIP_BYTES_W) begin
                        if (rnd_pass2) begin
                            // the clip is complete: nothing is retained
                            discard_render;
                            session <= S_READY;
                        end else begin
                            rnd_pass1_done <= 1'b1;
                        end
                    end
                    enq_rsp(e_cmd, e_seq, 8'd0);
                end
            end
        end
    endtask

endmodule
