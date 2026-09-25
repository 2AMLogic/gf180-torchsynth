# Host transport client and behavioral-mock backend (software lane)

The host-side counterpart to the mock harness of
[MOCK-HARNESS.md](MOCK-HARNESS.md): the transport client that speaks protocol
version 2 over any transport satisfying the
[TRANSPORTS.md](TRANSPORTS.md) interface contract, and the alternate explorer
backend that routes the explorer render path through the client and the
behavioral mock. This document adds no wire bytes, no command code, and no
session rule; FRAMING.md, SESSION.md, and PATCH-LOAD.md remain the normative
behavior.

## Scope honesty

- The client and the mock are **software-lane verification tooling**. Physical
  UART/SPI/USB bindings are issue #81's deliverable and are unimplemented by
  construction; nothing here is evidence of hardware, RTL, synthesis fidelity,
  or sound playback.
- The audio transfer/streaming and render-trigger command set stays
  **unallocated** (issue #63). The client refuses any command outside the
  landed registry. Audio sourcing is a mock-boundary data path packed with the
  bound C1 sample codec, never a protocol command.

## Location and shape

- Host client: `src/torchsynth_voice/protocol_client.py`
  (`HostClient`, `FrameStream`, `MockTransport`, `ContractIdentity`).
- Mock-side audio sourcing: `src/torchsynth_voice/core_protocol_mock.py`
  (`MockCore.set_audio_source` / `audio_payload_bytes` / `audio_source_samples`,
  optional `audio_source` constructor argument).
- Alternate explorer backend: `src/torchsynth_voice/protocol_backend.py`
  (`build_protocol_mock_session`, `ProtocolMockRenderer`,
  `backend_contract_identity`, `backend_envelope`, `GOLDEN_AUDIO_VECTOR`,
  `TRANSPORT_BINDINGS`, `build_transport`, `transport_binding_identity`).
- Transport binding models: `src/torchsynth_voice/transport_binding_models.py`
  (`UartBindingTransport`, `SpiBindingTransport`, `UsbBindingTransport`):
  in-process conformance doubles that shape delivery exactly as the
  [TRANSPORTS.md](TRANSPORTS.md) binding sections do (continuous UART byte
  stream with noise resync; half-duplex SPI CS periods; USB bulk IN/OUT
  packet quantization), including the short write every binding can suffer
  (`fail_next_write_after`). They are not physical drivers — physical
  bindings are issue #81's deliverable — and they make no hardware claim;
  they exist to show the product model is invariant to which binding carries
  the bytes.
- Tests: `tests/test_protocol_client.py`, `tests/test_protocol_backend.py`,
  `tests/test_transport_bindings.py`.

The client is standard-library only. Everything environment-shaped
(transport, clocks, timeouts, retry budgets, contract identity) is injected as
constructor arguments; no transport is implicitly configured.

## Client rules

- **Framing**: the client reassembles frames from arbitrary transport chunks
  by scanning for `sync` and validating the frame CRC; a corrupted frame is
  dropped whole and counted, never partially applied. A partial `sync` prefix
  at a chunk boundary is retained. Frames larger than the transport's
  `max_frame_bytes` are never emitted.
- **Negotiation gates**: a fatal `ERR_PROTOCOL_VERSION` — wrong header
  version, mismatched `numeric_contract_version` (including the stale
  `unbound:#53` marker), or a `READY` announcing a different contract — closes
  the client session. A configured `expected_profile` that the `READY` payload
  does not confirm is a client-side refusal: the host stops; there is no
  renegotiation mid-session.
- **Sequences and retries**: sequence values are strictly increasing per
  session, wrapping modulo 65536. `ERR_BUSY` re-sends the identical frame
  (same sequence and bytes) until a bounded retry budget is spent. A response
  timeout re-sends the identical frame for idempotent commands
  (`HELLO`, `RESET`, `PATCH_ABORT`) only; a timed-out non-idempotent command
  raises instead of guessing, because a retry cannot reuse stale state.
- **Partial write**: [TRANSPORTS.md](TRANSPORTS.md) requires `send` to return
  only once the link has accepted every byte, or to raise. A transport that
  placed only a prefix on the wire raises `TransportWriteError`, and the
  client treats the command as **never delivered** — which it provably is,
  because the receiver drops a truncated frame whole on the `sync`/CRC rule
  and can therefore have applied nothing. Session state does not advance. The
  identical frame (same sequence, same bytes) is re-sent for idempotent
  commands within a bounded `write_retries` budget; a partially written
  non-idempotent command raises to the host, which re-sends the whole
  transaction on a fresh `transaction_id` rather than resuming staged state.
- **Session state**: the client enforces the SESSION.md lifecycle table
  locally and fails loudly on violations (`patch` frames require
  `patch_open`, `PATCH_OPEN` requires `ready`, `RESET` requires a live
  session). `ERR_PATCH_HASH_MISMATCH` and `ERR_TX_TIMEOUT` are tracked as
  transaction-ending: the client returns to `ready` and never reuses the
  staged state.
- **Transactions**: `commit_patch` computes the bound, domain-separated patch
  hash ([PATCH-LOAD.md](PATCH-LOAD.md)) from the exact staged words and the
  negotiated `numeric_contract_version`; the host re-sends whole transactions
  keyed on a fresh `transaction_id` after any discard.

## Mock audio sourcing

`MockCore.set_audio_source` stores fixed golden vectors as exact rationals and
packs them on demand through the bound C1 codec (`encode_audio_payload`,
3-byte little-endian Q2.21, half-even, saturating). The source is validated as
finite reals; a truncated or padded payload fails decode. This models a data
source at the core boundary so the codec and its packing rule are testable
before issue #63 allocates transfer commands; it is not RTL behavior and not a
fidelity claim.

## Transport selection is a configuration act

[TRANSPORTS.md](TRANSPORTS.md) requires the framing and session layers to run
unchanged on any conforming transport, so "choosing among UART/SPI/USB is a
configuration act, not a protocol change". The software lane makes that
checkable rather than asserted: the product model itself carries the choice.

- `protocol_backend.TRANSPORT_BINDINGS` names every modeled carrier —
  `loopback` (the in-memory `MockTransport` pipe) plus the `uart`, `spi` and
  `usb` binding models. `build_transport(core, binding)` constructs one;
  an unmodeled name is refused by listing the modeled ones, never silently
  defaulted, so a carrier nobody modeled can never look like one that was.
- Every carrier is configured with the same `max_frame_bytes`
  (`TRANSPORT_MAX_FRAME_BYTES`), so any behavioral difference between them
  could only come from delivery shape, never from a differently sized
  envelope.
- `build_protocol_mock_session(store_root, transport=…)` and
  `explorer.build_session(…, transport=…)` accept the name;
  `tools/explore.py --transport {loopback,uart,spi,usb}` exposes it on the
  shipped CLI. A backend that speaks no wire protocol has no carrier to name
  and **refuses** the argument rather than accepting one it would ignore.
- The negotiated contract identity is deliberately independent of the
  carrier: `transport` is a separate envelope field
  (`binding`, `kind: software-lane-binding-model`,
  `physical_link: none (issue #81)`, `hardware_claim: none`), reported beside
  `contract`, never folded into it. It is `null` for a backend with no wire
  protocol.

Verified end to end in `tests/test_protocol_backend.py`
(`TransportSubstitutabilityTests`, `ExplorerTransportCliTests`): the same
seeded session over all four carriers yields the same published reference, the
same `show()` payload, byte-identical bookmark files, the same `repeat`
reference, the same 78-value applied patch, the same negotiated contract
identity, and the **byte-identical client command-frame stream** — and each of
those still equals the Python (`fake`) backend's, which has no carrier at all.

## Alternate explorer backend

`build_protocol_mock_session(store_root)` wires an `ExplorerSession` whose
renderer publishes the deterministic synthetic fixture of the landed fake mode
and then drives the full protocol path against the mock: `HELLO`/`READY`
negotiation, one name-keyed patch transaction staging all 78 canonical
parameter values as bound Q10.21 words keyed on the inventory's SHA-256 table
reference, atomic commit under the bound patch hash, and `RESET`. Any protocol
failure fails the render; a previous valid selection is never replaced. The
mock is configured with the fixed golden audio vector.

The backend envelope shows the honest labels: the backend name
(`protocol-mock-v2-behavioral-synthetic-audio`), the contract identity
(protocol version, numeric-contract version digest, name-table digest,
`kind: behavioral-mock-software-only`, `hardware_claim: none`), and the audio
block identifying the source as fixed golden vectors at the mock boundary.
Repeat/save behavior is identical to the Python fake backend when identities
match: both publish the same deterministic synthetic fixture, and the bookmark
documents are byte-identical.

## Explorer display surface

`build_session(store_root, backend="protocol-mock")` selects this backend from
the explorer's own wiring seam, and `tools/explore.py --backend protocol-mock`
exposes it on the shipped CLI ([../EXPLORER-MVP.md](../EXPLORER-MVP.md)). Every
CLI envelope carries a `contract` field beside the existing `backend` label:
the negotiated contract identity for this backend, and `null` for a backend
that speaks no wire protocol (`docker`, `fake`, `none`) — a backend without a
protocol never borrows this one's identity. A `transport` field sits beside it
on the same rule: the binding identity of the carrier that actually moved the
frames, `null` for a backend with no carrier. The render-call counter is named
for the renderer that actually ran
(`protocol_mock_renderer_calls_this_process`, distinct from the fake mode's
`fake_renderer_calls_this_process`). The rendering path, bookmark
schema, session vocabulary and holdout refusals of the MVP are unchanged: the
protocol-mock backend publishes the MVP's own synthetic fixture and adds the
protocol round trip as verification, so repeat/save output is byte-identical
to `--backend fake` for the same identity.

## Acceptance-criteria mapping (issue #66)

| Criterion | Where verified |
| --- | --- |
| Golden protocol frames round-trip byte exactly across client/mock | pinned golden HELLO frame, reference-encoder comparisons, and byte-exact READY/transaction tests in `tests/test_protocol_client.py` |
| Version/profile/patch-hash mismatch, partial write, timeout, stale state, corrupted audio fail | refusal tests: fatal contract negotiation gates, expected-profile refusal, hash-mismatch discard, one-byte-fragment delivery, identical-frame idempotent retry, stale sequence/transaction-id refusal, expired-transaction continuation → `ERR_TX_TIMEOUT` with the client dropping stale state, truncated/tampered audio payload. **Partial write** specifically (`PartialWriteTests` in `tests/test_protocol_client.py`, partial-write tests in `tests/test_transport_bindings.py`): a short write raises `TransportWriteError` at every truncation point of a frame, the truncated prefix never becomes a frame at the receiver, an idempotent command is re-sent byte-identically and a non-idempotent one is never retried, and after a short write on UART/SPI/USB the accepted frames, the core→host stream and the applied patch equal the clean run's |
| Mock sources audio from fixed golden vectors, not pretending to be RTL | `GOLDEN_AUDIO_VECTOR` through `MockCore.set_audio_source`; decode/re-encode byte-exactness; audio block labeled as a mock-boundary data path |
| Explorer shows backend and contract identity | the shipped CLI: `tools/explore.py --backend protocol-mock` prints `backend` and the negotiated `contract` identity in every envelope, and a backend with no wire protocol prints `contract: null` (`ExplorerBackendDisplayTests` in `tests/test_protocol_backend.py`); the library `backend_envelope` label + `backend_contract_identity` agree with it |
| Transport swap without changing the product model | two levels. **Protocol stack** (`tests/test_transport_bindings.py`): the same full canonical session over `MockTransport` and the UART/SPI/USB binding models gives identical client frame streams, identical core response streams and identical final core state, through mid-frame packet/CS cuts, multi-frame IN transfers and CS periods, and end-to-end UART noise resync. **Product model** (`TransportSubstitutabilityTests` / `ExplorerTransportCliTests` in `tests/test_protocol_backend.py`): selecting the carrier by name at the backend, explorer and `tools/explore.py --transport` seams leaves the published reference, `show()` payload, bookmark bytes, `repeat` reference, applied 78-value patch, contract identity and client frame stream identical across all four carriers and equal to the Python backend's; an unmodeled binding is refused by name and a backend without a wire protocol refuses the flag |
| Repeat/save identical across Python and mock backends when identities match | fake vs protocol-mock bookmark byte-equality and repeat reference equality at the library seam, and end to end through the CLI: the same seed gives an equal `show()` payload, byte-identical bookmark files and an equal `repeat` payload across `--backend fake` and `--backend protocol-mock`, with zero renderer calls on save/repeat |
