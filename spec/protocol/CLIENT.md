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
  `backend_contract_identity`, `backend_envelope`, `GOLDEN_AUDIO_VECTOR`).
- Tests: `tests/test_protocol_client.py`, `tests/test_protocol_backend.py`.

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
documents are byte-identical. The landed explorer MVP CLI
([../EXPLORER-MVP.md](../EXPLORER-MVP.md)) is unchanged; the backend is a
library seam, and any CLI surface remains future work.

## Acceptance-criteria mapping (issue #66)

| Criterion | Where verified |
| --- | --- |
| Golden protocol frames round-trip byte exactly across client/mock | pinned golden HELLO frame, reference-encoder comparisons, and byte-exact READY/transaction tests in `tests/test_protocol_client.py` |
| Version/profile/patch-hash mismatch, partial write, timeout, stale state, corrupted audio fail | refusal tests: fatal contract negotiation gates, expected-profile refusal, hash-mismatch discard, one-byte-fragment delivery, identical-frame idempotent retry, stale sequence/transaction-id refusal, truncated/tampered audio payload |
| Mock sources audio from fixed golden vectors, not pretending to be RTL | `GOLDEN_AUDIO_VECTOR` through `MockCore.set_audio_source`; decode/re-encode byte-exactness; audio block labeled as a mock-boundary data path |
| Explorer shows backend and contract identity | `backend_envelope` backend label + `backend_contract_identity` in `tests/test_protocol_backend.py` |
| Transport swap without changing the product model | same client session over `MockTransport` chunk sizes and the scripted lossy transport; identical frames and outcomes |
| Repeat/save identical across Python and mock backends when identities match | fake vs protocol-mock bookmark byte-equality and repeat reference equality |
