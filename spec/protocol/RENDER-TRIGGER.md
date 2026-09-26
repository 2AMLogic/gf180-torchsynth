# Render trigger and host-fed noise stream: the pass/digest binding

Normative for the two commands that carry DR-0010's clip-lifecycle
obligation on the transport lane: `RENDER_TRIGGER` (`0x15`) and
`NOISE_STREAM` (`0x16`). DR-0010
([`../decision-records/0010-one-shot-rtl-microarchitecture.md`](../decision-records/0010-one-shot-rtl-microarchitecture.md),
"Noise re-feed obligation", "Clip lifecycle", "Host protocol binding")
records the obligation and assigns the command definition to the #66
transport lane; this document is that definition (issue #188, the lane's
successor issue). It changes no target, arithmetic profile, noise policy,
normalization mechanic, parameter ordering, or clip timing: the noise stream
stays host-fed bit-exactly under DR-0003 / DR-0008 C8, and the two-pass
normalization replay stays DR-0010 P4.

## What is bound, and what is deliberately not decided here

DR-0010 requires that one trigger be bound to **one sound identity and one
noise-stream digest**, that pass 1 and pass 2 of that trigger and no others
may write the output stream, and that a digest mismatch between the passes
discard the clip entire. This document binds the **transport-declared**
digest:

- The host declares the noise-stream digest in each pass's `RENDER_TRIGGER`.
  The core latches the pass-1 declaration and compares the pass-2
  declaration against it, together with the sound identity, **before it
  accepts any pass-2 noise byte** — therefore before any pass-2 sample
  exists, and output release only ever happens in pass 2 (DR-0010 "output
  release begins only after the branch decision").
- The core does **not** compute a digest over the noise bytes it actually
  receives. Whether it should is an **open design question** DR-0010 does
  not settle: an on-chip digest over 2 x 705,600 B per clip is not in
  DR-0010's worst-case resource table nor in the #75 owner row, so answering
  "receiver-side" requires a DR-0010 amendment before any implementation
  (DR-0010 "Change control"). That question is tracked separately
  (issue #207); nothing in this document forecloses either answer — a
  receiver-side check would compare its computed digest against exactly the
  declared digest bound here.

Residual limit of the declared-digest binding, stated rather than hidden: a
host that declares the same digest in both passes but feeds different bytes
in pass 2 is **not detected core-side**. Byte-level truth lives on the host,
at the pre-render gate `noise_stream_golden.check_fed_bytes` (length,
declared digest over the actual bytes, C8 slot framing, canonical seed-13
identity), and in the conforming host client, which derives the declared
digest from the exact bytes it transmits and re-sends those same bytes for
pass 2. Within one stream, the #75 noise lane (`tb/sv/noise_stream_dut.sv`)
additionally binds the sound index and slot per cycle (sticky
`IDENTITY_CHANGED`).

## Capability

Both commands are gated by capability bit 2, `CAP_RENDER` (`0x0004`),
negotiated in `HELLO`/`READY` like every other capability
([FRAMING.md](FRAMING.md)). A core that does not grant it answers both codes
with `ERR_UNSUPPORTED_COMMAND` and changes nothing. A core that grants it
must implement everything in this document.

## `RENDER_TRIGGER` (`0x15`)

Host → core. Payload:

| Field | Encoding | Meaning |
| --- | --- | --- |
| `pass_index` | `u8` | `1` (pass 1, peak tracking) or `2` (pass 2, replay); any other value is `ERR_PAYLOAD_LENGTH` |
| `sound_identity` | opaque byte string (`sound_identity` placeholder) | the global sound identity this trigger renders |
| `noise_stream_sha256` | opaque byte string, exactly 32 bytes | the declared SHA-256 over the clip's complete host-fed noise byte stream (the same digest `noise_stream_golden.noise_bytes_sha256` produces and `check_fed_bytes` verifies) |

A `noise_stream_sha256` of any length other than 32, or trailing bytes, is
`ERR_PAYLOAD_LENGTH`; the core changes nothing.

The core answers success with an empty-payload response (`kind = 0x02`,
same `command` and `sequence`).

## `NOISE_STREAM` (`0x16`)

Host → core. Payload:

| Field | Encoding | Meaning |
| --- | --- | --- |
| `pass_index` | `u8` | must equal the open render's current pass |
| `offset` | `u32` | byte offset of `data` within this pass's stream; must equal the number of bytes the core has accepted in this pass |
| `data` | opaque byte string, 1 or more bytes | the next bytes of the stream, verbatim (little-endian binary32 samples, C8) |

`NOISE_STREAM` carries noise bytes only; it is not the audio output
transfer command, which stays unallocated ([FRAMING.md](FRAMING.md)). The
core consumes the bytes as they arrive and retains no clip buffer (DR-0010
Memory strategy: on-chip retention of the stream is rejected).

## Render lifecycle

The session lifecycle of [SESSION.md](SESSION.md) gains one state,
`rendering`, with a current pass (1 or 2) and a per-pass accepted-byte
count. The per-pass stream length is the product profile's clip length:
`SCHED_SAMPLES_PER_PASS x 4` = 176,400 x 4 = **705,600 B** (DR-0003
acceptance item 3; DR-0010 schedule).

| State | Command | Condition | Outcome |
| --- | --- | --- | --- |
| `ready` | `RENDER_TRIGGER` pass 1 | a committed patch is active and its `sound_identity` equals the trigger's | render opened: bind (`sound_identity`, `noise_stream_sha256`); current pass 1, 0 bytes accepted; → `rendering` |
| `ready` | `RENDER_TRIGGER` pass 1 | no committed patch | `ERR_BAD_STATE`; nothing changes |
| `ready` | `RENDER_TRIGGER` pass 1 | active patch has a different `sound_identity` | `ERR_RENDER_BINDING`; nothing changes (no clip existed) |
| `ready` | `RENDER_TRIGGER` pass 2, or `NOISE_STREAM` | — | `ERR_BAD_SEQUENCE`; nothing changes (no render is open) |
| `rendering` | `NOISE_STREAM` | `pass_index` = current pass, `offset` = bytes accepted, and the bytes fit within 705,600 | bytes accepted; when pass 2 reaches 705,600 the clip is **complete** and the core → `ready` |
| `rendering` | `NOISE_STREAM` | wrong `pass_index`, non-contiguous `offset`, or bytes past 705,600 | `ERR_NOISE_STREAM`; the clip is **discarded entire**; → `ready` |
| `rendering` | `RENDER_TRIGGER` pass 2 | pass 1 has accepted exactly 705,600 B, and `sound_identity` **and** `noise_stream_sha256` equal the pass-1 binding | current pass 2, 0 bytes accepted |
| `rendering` | `RENDER_TRIGGER` pass 2 | `sound_identity` or `noise_stream_sha256` differs from the pass-1 binding | `ERR_RENDER_BINDING`; the clip is **discarded entire**; → `ready` |
| `rendering` | `RENDER_TRIGGER` pass 2 | pass 1 short of 705,600 B, or pass 2 already open | `ERR_NOISE_STREAM`; the clip is **discarded entire**; → `ready` |
| `rendering` | `RENDER_TRIGGER` pass 1 | — | `ERR_BAD_STATE`; the open render is unchanged (a second trigger never joins or replaces a clip; the host resets to abandon it) |
| `rendering` | `PATCH_OPEN` | — | `ERR_BAD_STATE`; the open render is unchanged |
| `rendering` | `RESET`, or `HELLO` | — | all render state discarded (DR-0010: no resumable render); `RESET` → `ready`, `HELLO` renegotiates |

Rules:

- **Pass 1 and pass 2 of the bound trigger and no others.** A render has
  exactly one binding, fixed at its pass-1 trigger. A pass-2 trigger either
  matches it exactly or discards the clip; there is no way to rebind, extend,
  or splice a clip. After a clip completes or is discarded the core is in
  `ready` and the next clip needs a fresh pass-1 trigger.
- **"Discarded entire."** A discarded clip has no valid output: no sample of
  it may be presented as part of a clip, and the host discards anything it
  already received for that trigger. Under the declared-digest binding the
  pass-2 digest check happens before pass 2 starts, so a digest or identity
  mismatch discards a clip before any sample of it is released. A
  `NOISE_STREAM` fault during pass 2 can arrive after some pass-2 samples
  were released; the clip is still discarded entire, and only a completed
  pass 2 makes a clip valid.
- **Pass 2 re-feeds the identical stream.** The host re-sends the same
  705,600 B under the same declared digest (DR-0010 "an idempotent re-send
  of the identical bytes under the same digest binding").
- **Retry.** Neither command is idempotent. `ERR_BUSY` is retried with the
  identical frame (the command was never enqueued). A render whose command
  timed out is abandoned by `RESET` and re-triggered from pass 1. A render
  is never resumed ([SESSION.md](SESSION.md) stale-state rule).
- **Ordering.** Render frames must not interleave with patch-transaction
  frames: `PATCH_OPEN` in `rendering` is `ERR_BAD_STATE`, and render
  commands in `patch_open` are `ERR_BAD_STATE`.

## Error codes

Added to the [SESSION.md](SESSION.md) error table:

| Code | Name | Meaning | Recovery |
| --- | --- | --- | --- |
| `0x0E` | `ERR_RENDER_BINDING` | a pass-2 trigger's `sound_identity` or declared `noise_stream_sha256` differs from the pass-1 binding, or a pass-1 trigger's `sound_identity` differs from the active patch's | open clip discarded entire; core → `ready`; host re-triggers from pass 1 |
| `0x0F` | `ERR_NOISE_STREAM` | noise-stream framing fault: wrong pass, non-contiguous offset, bytes past the clip length, or a pass-2 trigger before pass 1 is complete | open clip discarded entire; core → `ready`; host re-triggers from pass 1 |

Both are control-plane outcomes: they carry no numeric-contract meaning,
and neither closes the session.

## Where the binding reaches the RTL

The replay controller (`tb/sv/normalization_replay_engine.sv`, #77) has a
`bind_reject` input for this binding (added with this document). A receiver
that answers `ERR_RENDER_BINDING` or `ERR_NOISE_STREAM` for an open render
pulses `bind_reject`: while the engine is in pass 1 or pass 2 it raises
sticky error code 3 (`ERR_BINDING_REJECTED`), returns to idle, asserts no
further `audio_out_valid` and never asserts `done` for that render; a later
`start` is refused while the error is sticky. With no render open (idle or
done) `bind_reject` is ignored, matching the "no clip existed, nothing is
discarded" rows above. A rejection at the pass-1/pass-2 boundary, where the
declared-digest check happens, therefore releases **zero** samples. The
sticky error clears only on `rst`, which is how the receiver completes the
discard. The engine holds no digest and computes none.

The RTL protocol front end (`tb/sv/patch_control.sv`, #69) grants
`CAP_RENDER` (`CAPS_GRANTED` = bits 0, 1 and 2) and implements the receiver
for both codes: the `rendering` session state with its current pass and
per-pass accepted-byte count, the pass-1 latch of the declared 32-byte
digest and the bound sound identity, the pass-2 compare of both, and every
error row of the lifecycle table above. It computes no digest of its own
and holds no clip buffer — `NOISE_STREAM` data bytes are never stored, so
the receiver's entire per-clip live state is 810 bits (256 declared digest
+ 512 bound identity + 8 identity length + 1 current pass + 1 pass-1-complete
+ 32 accepted-byte count), inside DR-0010 P2's "under ~1 Kbit" figure.

The wire itself is `tb/sv/render_binding_top.sv`: a structural top that
declares exactly one composition, the receiver's `bind_reject` output driving
the engine's `bind_reject` input with no re-timing or gating in between. It
is not the one-shot product top and adds no owner row to DR-0010's module
table; the noise **byte** stream (#75 lane) and the pre-normalization mix
**sample** stream (#76 lane) remain separate declared interfaces that no
landed RTL joins.

Because capabilities are negotiated rather than asserted, a host that does
not request `CAP_RENDER` in `HELLO` still receives `ERR_UNSUPPORTED_COMMAND`
for both codes and the `READY` capability word it is answered with is
`requested & granted`, not the static grant mask.

## Status

Verified in software and in PDK-free RTL simulation (Icarus Verilog):

- The behavioral mock core (`core_protocol_mock.MockCore`) and host client
  (`protocol_client.HostClient.render_clip`) implement this document
  (`tests/test_render_trigger.py`).
- The replay engine's `bind_reject` discard is exercised by `tb/run_tb.py
  normreplay` (boundary, mid-pass-2 and mid-pass-1 rejections, plus a mutant
  that ignores `bind_reject` and must be detected).
- The RTL receiver (`tb/sv/patch_control.sv`) is compared byte- and
  cycle-exactly against its Python mirror
  (`src/torchsynth_voice/patch_control_model.py`) by `tb/run_tb.py patch`
  over the whole lifecycle table: `CAP_RENDER` negotiation, pass-1 bind,
  contiguous `NOISE_STREAM` feed, pass-2 rebind and clip completion, every
  rejection row, and one completion of the product profile's real
  705,600-B/pass clip. Seven planted mutants (withheld capability, dropped
  pass-1 identity binding, dropped pass-2 digest or identity compare,
  dropped offset contiguity, suppressed `bind_reject`, dropped
  clip-completion transition) are each detected.
- The wire from receiver to engine is exercised end-to-end by `tb/run_tb.py
  normreplay` through `tb/sv/render_binding_top.sv`, driven by real
  `RENDER_TRIGGER`/`NOISE_STREAM` frames: a pass-2 declared-digest mismatch
  answers `ERR_RENDER_BINDING`, pulses `bind_reject` for exactly one cycle,
  raises the engine's sticky `ERR_BINDING_REJECTED` and releases **zero**
  samples, while the matching binding leaves the wire quiet and completes
  the clip bit-exactly. A mutant that drops the connection is detected.

None of this is evidence of synthesis, layout, signoff, hardware playback,
or sound fidelity.
