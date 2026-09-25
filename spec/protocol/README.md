# Core/host transport protocol — version 2, numerically bound

This directory specifies the transport protocol between the host and the
TorchSynth Voice core. It was first landed as the startable subset declared
on issue [#62](https://github.com/2AMLogic/gf180-torchsynth/issues/62) on
2026-09-19 (protocol version 1, placeholder widths). After #53 closed via
the DR-0008 ratification (Accepted by reviewed merge, 2026-09-21), protocol
version 2 replaced the placeholder policy with the accepted numeric binding:
no DSP semantics are invented, and every bound value comes from the accepted
register. The normative behavioral target remains
[`../VOICE-CONTRACT.md`](../VOICE-CONTRACT.md); `spec/` owns this behavior
and code alone may not change it.

## Documents

| Document | Content |
| --- | --- |
| [FRAMING.md](FRAMING.md) | Frame envelope schema, integer and opaque-byte encoding, endianness, field order, versioning, HELLO/READY negotiation, command registry |
| [SESSION.md](SESSION.md) | Session lifecycle, ordering, backpressure, timeouts, reset, idempotency, error codes and recovery, live-note exclusion |
| [PATCH-LOAD.md](PATCH-LOAD.md) | Name-keyed patch load transaction and the versioned canonical name table structure |
| [TRANSPORTS.md](TRANSPORTS.md) | Transport-agnostic interface contract and UART/SPI/USB binding requirements |
| [MOCK-HARNESS.md](MOCK-HARNESS.md) | The software mock round-trip harness: contract, numeric binding policy, acceptance-criteria mapping |
| [CLIENT.md](CLIENT.md) | The host transport client (software lane) and the alternate explorer backend over the behavioral mock |
| [RENDER-TRIGGER.md](RENDER-TRIGGER.md) | The render trigger and host-fed noise stream commands carrying DR-0010's pass/digest binding (transport-declared digest) |

## Scope boundary

The version 1 startable subset (framing; command sequencing and ordering;
idempotency; error semantics; the name-keyed patch load path and canonical
name table structure; version/capability negotiation; transport separation;
and the software mock round-trip harness) is unchanged in structure. Version
2 binds the regions #53 had gated:

- sample payload packing: the audio sample word is 24-bit Q2.21
  little-endian (DR-0008 C1, accepted);
- `patch_value` is the uniform 32-bit Q10.21 host-entry word (DR-0008 C4,
  C6, C7, accepted);
- `numeric_contract_version` is the SHA-256 of the accepted machine-readable
  DR-0008 register, and the negotiation gate refuses any other value —
  including the stale `unbound:#53` marker — as a fatal version error;
- the patch hash is a domain-separated SHA-256 bound to the negotiated
  numeric contract (replacing the version 1 stand-in digest).

Still **not** specified here:

- the audio output transfer/streaming command set (its numeric format is
  bound; no landed record allocates the commands — DR-0010, the issue #63
  record, did not);
- whether the core computes a digest over the noise bytes it receives, in
  addition to binding the transport-declared digest (open, issue #207;
  requires a DR-0010 amendment if answered receiver-side);
- per-parameter wire widths and numeric IDs (the name table's `numeric_id`
  and `width` columns stay reserved);
- the global sound identity binding (issues #12/#88), the product profile
  naming, and the device lock-state semantics — `sound_identity`,
  `profile_id`, and `locks` remain opaque byte strings;
- live-note semantics: absent, per [SESSION.md](SESSION.md).

## Status

These documents specify control-plane behavior plus the numeric wire binding
consumed from the accepted DR-0008 register. They qualify the protocol only:
they are not evidence of synthesis fidelity, RTL equivalence, or hardware
playback. The render-trigger/noise-stream commands implement the transport
lane's share of DR-0010 ("Noise re-feed obligation", "Clip lifecycle"); the
audio output transfer/streaming command set stays unallocated.
