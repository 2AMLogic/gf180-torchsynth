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
- Tests: `tests/test_core_protocol.py`, `tests/test_core_protocol_mock.py`.

The harness has no dependencies beyond the Python standard library, in line
with the reference tooling package. It injects everything environment-shaped
(clock, queue depth, capabilities, name table) as constructor arguments; no
backend is implicitly configured.

## Placeholder policy

The harness encodes every numeric-gated region as an opaque byte string or a
named placeholder width, exactly as [FRAMING.md](FRAMING.md) requires:

- `patch_value` bytes are carried and compared verbatim; the mock never
  interprets them.
- The patch hash uses SHA-256 as a **named stand-in algorithm**; the final
  binding of the hash computation is deferred to #53 and the stand-in must be
  replaced, not relied on, when that decision record lands.
- `numeric_contract_version` is the reserved ASCII value `unbound:#53` in the
  mock's default negotiation; a mismatch against any other value is
  negotiable equality, not an error, until #53 defines the real semantics.

Any concrete numeric interpretation sneaking into these regions is a spec
violation, not an implementation convenience.

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

## Acceptance-criteria mapping (issue #62, subset items)

| Criterion | Where verified |
| --- | --- |
| Framing, endianness, field order explicit | byte-exact encode/decode tests in `tests/test_core_protocol.py` |
| Version mismatch rejected | `HELLO` with wrong `version` → fatal `ERR_PROTOCOL_VERSION`, core `closed` |
| Hash mismatch rejected | `PATCH_COMMIT` with wrong declared hash → `ERR_PATCH_HASH_MISMATCH`, staged state discarded |
| Repeat/retry cannot reuse stale state or mix partial patches | replay, stale-sequence, duplicate-name, timeout, and reset tests in `tests/test_core_protocol_mock.py` |
| Backpressure, timeout, reset, session semantics | `ERR_BUSY`, injected-clock transaction expiry, `RESET` tests |
| Transport separable | the mock speaks only the transport interface contract; bindings are unimplemented by construction |
| Live-note semantics absent | the command registry contains no note command and the tests pin the full registry |
