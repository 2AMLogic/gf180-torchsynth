# Transport separation and UART/SPI/USB interface contracts

The protocol suite of this directory is transport-agnostic: it defines byte
frames and session semantics, and it must remain implementable over any
transport that satisfies the interface contract below. No UART, SPI, or USB
detail may leak into [FRAMING.md](FRAMING.md), [SESSION.md](SESSION.md), or
[PATCH-LOAD.md](PATCH-LOAD.md), and no transport may interpret frame contents.

## Transport interface contract

A transport implementation provides, to the framing layer:

| Operation | Contract |
| --- | --- |
| `send(bytes)` | Transmits the given bytes; returns only when the bytes are accepted by the physical link (or raises). Does not split, reorder, or reframe. |
| `recv()` | Yields received bytes in arrival order, blocking until at least one byte is available. Byte boundaries yielded need not correspond to frame boundaries. |
| `max_frame_bytes` | The largest well-formed frame the link can carry in one direction; framing must not emit larger frames. |

Required transport properties:

- **Ordering**: bytes are delivered in transmission order, or the transport
  declares the link broken.
- **Integrity boundary**: error detection is the frame CRC
  ([FRAMING.md](FRAMING.md)). A transport may additionally report link errors,
  but a transport that silently corrects bytes is nonconforming.
- **No interpretation**: a transport never inspects, transforms, repackets, or
  re-times frame contents beyond byte delivery.
- **Substitutability**: the framing and session layers must run unchanged on
  any conforming transport; choosing among UART/SPI/USB is a configuration
  act, not a protocol change.

## UART binding

- Plain byte stream; no message boundaries exist at the link. Receivers must
  resynchronize solely by scanning for `sync` and validating the frame CRC.
- The host and core must agree on a bit rate outside this protocol; the
  protocol has no bit-rate field and requires none. Break conditions, if the
  UART reports them, are surfaced as link errors, not as protocol resets.
- Idle-line time is meaningless to the protocol; only the session and
  transaction timeouts of [SESSION.md](SESSION.md) apply.

## SPI binding

- The core is a peripheral; the host is the controller and drives the clock.
  Full-duplex operation is permitted but not required: bytes the core sends
  while the host sends a command travel in the same transfer only when the
  binding's half-duplex discipline allows it.
- Chip-select assertion SHOULD frame one or more whole frames; a receiver
  must nevertheless validate every frame by `sync` and CRC and must not
  assume CS boundaries align with frame boundaries.
- Word size is 8 bits. Bit order is MSB-first on the wire; the frame's own
  little-endian integer rule applies to the byte stream after deserialization.

## USB binding

- One bulk OUT endpoint (host→core commands) and one bulk IN endpoint
  (core→host responses). USB packet boundaries exist but are not frames;
  receivers must reassemble the byte stream and validate frames by `sync` and
  CRC exactly as on UART.
- Control transfers must not carry protocol frames; the protocol's
  negotiation and error semantics are the only session-control path, so that
  the session layer cannot distinguish transports.
- A USB reset or disconnect is a transport loss, not a protocol `RESET`: on
  re-enumeration the session starts `closed` and the host renegotiates.

## What transports may not do

No binding may carry per-command metadata out of band (sideband reset lines
carrying semantic state, transport-level command queues, or transport-retried
frames without the idempotency rules of [SESSION.md](SESSION.md)). All
session-visible behavior must be identical across the three bindings; this is
what keeps the transport separable and the mock harness ([MOCK-HARNESS.md](MOCK-HARNESS.md))
representative of the real links at the byte-frame level.
