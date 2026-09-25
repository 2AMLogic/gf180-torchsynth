# Software mock round-trip harness

The mock harness is a standard-library Python model of the frame, session,
and patch-transaction rules of this directory, used to verify protocol
behavior before hardware exists. It qualifies the **protocol**, not the core:
it proves the documented rules are self-consistent and testable, and it is
explicitly not evidence of synthesis fidelity, RTL equivalence, or hardware
playback.

## Location and shape

- Framing, encoding, command registry, negotiation structures, and error
  codes: `src/torchsynth_voice/core_protocol.py`.
- The mock core (session state machine, staged patch transactions,
  backpressure, timeouts, idempotency): `src/torchsynth_voice/core_protocol_mock.py`.
- The host transport client over the [TRANSPORTS.md](TRANSPORTS.md) interface
  contract, and the alternate explorer backend riding this mock:
  [CLIENT.md](CLIENT.md), `src/torchsynth_voice/protocol_client.py`,
  `src/torchsynth_voice/protocol_backend.py`.
- Tests: `tests/test_core_protocol.py`, `tests/test_core_protocol_mock.py`,
  `tests/test_protocol_client.py`, `tests/test_protocol_backend.py`,
  `tests/test_transport_bindings.py`.

The harness has no dependencies beyond the Python standard library, in line
with the reference tooling package. It injects everything environment-shaped
(clock, queue depth, capabilities, name table) as constructor arguments; no
backend is implicitly configured.

## Numeric binding policy

Protocol version 2 binds the regions protocol version 1 carried as
placeholders. The harness encodes them exactly as [FRAMING.md](FRAMING.md)
requires, consuming the accepted DR-0008 register only through the refusal
gate in `torchsynth_voice.fixedpoint.choices`:

- `numeric_contract_version` is the 32-byte SHA-256 of
  `spec/reference/fixedpoint-choices-v1.json`, computed through the gate: a
  register that is not Accepted raises instead of binding.
- `patch_value` words are exactly 4 bytes (32-bit Q10.21, little-endian);
  the mock refuses any other width with `ERR_PAYLOAD_LENGTH` and stores the
  word verbatim, never interpreting it.
- The patch hash is the bound digest: SHA-256 over the
  `gf180-torchsynth/patch-hash-v2` domain tag, the negotiated
  `numeric_contract_version`, and the staged concatenation. This replaces
  protocol version 1's stand-in (undomain-separated SHA-256 over the staged
  concatenation only), per this document's stated successor path: a hash is
  valid only under the negotiated numeric contract.
- The audio sample codec packs 3-byte little-endian Q2.21 words
  (half-even rounding, saturating) per DR-0008 C1.
- `sound_identity`, `profile_id`, and `locks` remain opaque byte strings
  carried verbatim; their deferring decisions did not gate on #53.

## Round-trip contract

`MockCore` exposes the core boundary at the transport interface contract's
level:

- `submit(frame_bytes) -> bytes | None` enqueues one command frame. If the
  receive queue is already at `rx_queue_depth`, the frame is not enqueued and
  the immediate `ERR_BUSY` response frame is returned; otherwise `None`.
- `poll() -> bytes | None` processes at most one queued command and returns
  its response frame, or `None` when the queue is empty.
- `handle_frame(frame_bytes) -> bytes` is the depth-1 convenience round trip:
  submit, then poll.

The host side uses the same `core_protocol` encoders the tests use. There is
no shortcut that bypasses byte-level framing — the byte level is the level
the tests must pin.

`MockCore` constructor arguments (all keyword, all defaulted for tests):

| Argument | Meaning |
| --- | --- |
| `name_table` | the accepted canonical name set (an iterable of names), built in tests from the committed parameter inventory |
| `capabilities` | granted capability bits |
| `max_payload` | payload cap advertised in `READY` |
| `rx_queue_depth` | command queue depth before `ERR_BUSY` |
| `patch_timeout_s` | open-transaction inactivity timeout |
| `clock` | zero-argument callable returning the current time; tests inject a controllable clock |

## Acceptance-criteria mapping (issue #62)

| Criterion | Where verified |
| --- | --- |
| Framing, endianness, field order explicit | byte-exact encode/decode tests in `tests/test_core_protocol.py` |
| Protocol carries profile, source/numeric-contract versions, sound identity, locks, and patch hash | `HELLO`/`READY`/transaction field tests; `numeric_contract_version` bound to the accepted register |
| Version mismatch rejected | `HELLO` with wrong header `version` → fatal `ERR_PROTOCOL_VERSION`, core `closed` |
| Stale placeholder rejected | `HELLO` with `unbound:#53` or any other mismatched `numeric_contract_version` → fatal `ERR_PROTOCOL_VERSION`, core `closed` |
| Hash mismatch rejected | `PATCH_COMMIT` with wrong declared hash → `ERR_PATCH_HASH_MISMATCH`, staged state discarded; a hash under a different contract cannot match |
| Repeat/retry cannot reuse stale state or mix partial patches | replay, stale-sequence, duplicate-name, timeout, and reset tests in `tests/test_core_protocol_mock.py` |
| Backpressure, timeout, reset, session semantics | `ERR_BUSY`, injected-clock transaction expiry, `RESET` tests |
| Transport separable | the mock speaks only the transport interface contract; bindings are unimplemented by construction |
| Live-note semantics absent | the command registry contains no note command and the tests pin the full registry |
