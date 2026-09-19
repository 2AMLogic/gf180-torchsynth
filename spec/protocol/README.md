# Core/host transport protocol — startable subset (pre-#53)

This directory specifies the transport protocol between the host and the
TorchSynth Voice core, scoped to the startable subset declared on issue
[#62](https://github.com/2AMLogic/gf180-torchsynth/issues/62) on 2026-09-19.
Everything here is arithmetic-independent: no DSP semantics are invented, and
no fixed-point width, scaling, or numeric parameter encoding is fixed. The
normative behavioral target remains [`../VOICE-CONTRACT.md`](../VOICE-CONTRACT.md);
`spec/` owns this behavior and code alone may not change it.

## Documents

| Document | Content |
| --- | --- |
| [FRAMING.md](FRAMING.md) | Frame envelope schema, integer and opaque-byte encoding, endianness, field order, versioning, HELLO/READY negotiation, command registry |
| [SESSION.md](SESSION.md) | Session lifecycle, ordering, backpressure, timeouts, reset, idempotency, error codes and recovery, live-note exclusion |
| [PATCH-LOAD.md](PATCH-LOAD.md) | Name-keyed patch load transaction and the versioned canonical name table structure |
| [TRANSPORTS.md](TRANSPORTS.md) | Transport-agnostic interface contract and UART/SPI/USB binding requirements |
| [MOCK-HARNESS.md](MOCK-HARNESS.md) | The software mock round-trip harness: contract, placeholder policy, acceptance-criteria mapping |

## Scope boundary

Declared startable subset (issue #62, `## Startable Subset`): message framing;
command sequencing and ordering; idempotency; error semantics; the name-keyed
patch load path and canonical name table structure; version/capability
negotiation with profile, source, and numeric-contract versions, patch hash,
and sound identity carried as opaque byte strings; transport separation; and a
software mock round-trip harness with placeholder widths.

Explicitly **not** specified here — each waits on #53 (the fixed arithmetic
and error contract decision record):

- sample payload packing widths and audio transfer/streaming wire formats;
- any fixed-point field widths, scaling constants, or numeric parameter
  encodings beyond opaque width placeholders;
- final numeric-contract version binding of the protocol header and hash
  computation.

Fields whose final form depends on #53 carry explicitly named placeholder
widths or opaque byte-string encodings; nothing in this directory may be read
as fixing them.

## Status

All documents in this directory are **unratified** until the #62 increment
merges and a later decision record binds the numeric contract. They specify
control-plane behavior only; they qualify no hardware and no synthesis
fidelity.
