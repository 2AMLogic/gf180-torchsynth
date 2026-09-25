# Core/host session semantics: ordering, backpressure, idempotency, errors

Normative session behavior above the frame envelope of
[FRAMING.md](FRAMING.md). Everything here is control-plane and
arithmetic-independent.

## Session lifecycle

| State | Entered by | Allowed host commands | Exits by |
| --- | --- | --- | --- |
| `closed` | power-up, fatal error, host silence after close | `HELLO` | `READY` sent → `ready` |
| `ready` | `HELLO` negotiation, transaction end, `RESET`, clip completed or discarded | `HELLO`, `RESET`, `PATCH_OPEN`, `RENDER_TRIGGER` (pass 1) | per command |
| `patch_open` | `PATCH_OPEN` accepted | `PATCH_NAME`, `PATCH_VALUE`, `PATCH_COMMIT`, `PATCH_ABORT`, `RESET` | commit/abort/timeout/error → `ready` |
| `rendering` | `RENDER_TRIGGER` pass 1 accepted (capability `render`) | `NOISE_STREAM`, `RENDER_TRIGGER` (pass 2), `RESET`, `HELLO` | pass 2 complete, render error, or `RESET` → `ready` ([RENDER-TRIGGER.md](RENDER-TRIGGER.md)) |

Rules:

- Exactly one session exists per core at any time. A `HELLO` during an open
  session acts as an implicit `RESET` and then negotiates.
- `RESET` returns the core to `ready` and discards any open patch
  transaction, staged or partially received, in full. Its response echoes the
  command's `sequence`.
- Only the fatal error (`ERR_PROTOCOL_VERSION`) leaves `closed` as the exit
  state; every other error returns the core to `ready` with any open
  transaction discarded.
- The core must never apply a state change as the result of a frame it has
  not fully and correctly received.

## Ordering

- The core processes commands in arrival order. There is one command stream;
  no out-of-order or parallel execution is defined in this subset.
- The host must send `sequence` values strictly increasing within a session,
  wrapping modulo 65536.
- Patch transaction frames of one transaction must not interleave with frames
  of another; a second `PATCH_OPEN` while a transaction is open is rejected
  with `ERR_PATCH_TX_ACTIVE` and changes nothing.

## Backpressure

- The core accepts at most `rx_queue_depth` commands (from `READY`) ahead of
  processing. A command arriving when the queue is full is not enqueued; the
  core answers it immediately with `ERR_BUSY`, echoing its `sequence`.
- A host that receives `ERR_BUSY` must retry the identical frame (same
  `sequence` and bytes) after a host-chosen delay, under the idempotency
  rules below. It must not advance `sequence` for the retry.
- There is no flow control on responses beyond the transport's own
  ([TRANSPORTS.md](TRANSPORTS.md)); the core never blocks on a host.

## Timeouts

- Host side: the host owns a per-command retransmission timeout. A retry
  re-sends the identical frame with the identical `sequence`; the
  idempotency rules make this safe.
- Core side: an open patch transaction that receives no transaction frame for
  `patch_timeout_ms` (from `READY`) expires: all staged state is discarded
  and the core returns to `ready`. The expiry is recorded as `ERR_TX_TIMEOUT`
  in the response to the next non-transaction command's error field only if
  the host attempts to continue the expired transaction; expiry itself is
  silent.
- A timeout must never leave partial patch state behind. Repeat and retry
  therefore cannot reuse stale state: a transaction resumed after expiry
  requires a fresh `PATCH_OPEN` with a fresh transaction id and full re-send
  of all names and values.

## Idempotency and retry

- The core caches, per session, the response to the most recent command of
  each idempotent class (`HELLO`, `RESET`, `PATCH_ABORT`) keyed by
  `sequence`. A re-received frame with an already-answered idempotent
  `sequence` and identical bytes replays the cached response and applies
  nothing.
- Patch frames are idempotent within an open transaction: a repeated
  `PATCH_NAME` or `PATCH_VALUE` with the same transaction id, name, and bytes
  is accepted as a no-op rewrite of the same staged slot. A repetition with
  different bytes for a name already staged is rejected with
  `ERR_DUPLICATE_NAME` and leaves the staged slot unchanged — a conflicting
  retry can never mix two versions of a value.
- After a transaction ends (commit, abort, timeout, or reset), replay of any
  of its `sequence` values answers `ERR_BAD_SEQUENCE` and applies nothing.
- `PATCH_COMMIT` is atomic: it applies the staged patch only if every
  declared name is staged and the computed patch hash matches the declared
  hash ([PATCH-LOAD.md](PATCH-LOAD.md)). Otherwise nothing is applied. A
  retried `PATCH_COMMIT` after a successful commit answers `ERR_BAD_SEQUENCE`
  rather than applying twice.
- Across sessions (after `RESET` or renegotiation), no cached state survives:
  every `sequence` restarts from the host's new stream, and stale frames from
  a previous session cannot be distinguished by sequence alone; the session
  boundary itself discards all caches, so a stale frame can only ever be
  answered as an unknown-sequence command in `ready` (`ERR_BAD_SEQUENCE` for
  transaction frames, since no transaction is open).

## Error responses

Error responses use `kind = 0x03`, echo `command` and `sequence`, and carry a
one-byte error code as the entire payload.

| Code | Name | Meaning | Recovery |
| --- | --- | --- | --- |
| `0x01` | `ERR_UNSUPPORTED_COMMAND` | command code undefined or not granted by negotiation | host stops using it; core state unchanged |
| `0x02` | `ERR_PROTOCOL_VERSION` | header `version` unsupported, or `HELLO` `numeric_contract_version` differs from the core's bound numeric contract ([FRAMING.md](FRAMING.md)) | **fatal**; core → `closed`; host must renegotiate with a supported version and the matching numeric contract |
| `0x03` | `ERR_BAD_FRAME` | decodable header, failed CRC or length | transport resync ([TRANSPORTS.md](TRANSPORTS.md)); core state unchanged; if a transaction was open it stays open and its timeout still applies |
| `0x04` | `ERR_BAD_SEQUENCE` | transaction frame with no open transaction, or sequence reuse outside idempotency rules | host re-synchronizes its sequence; core state unchanged |
| `0x05` | `ERR_BUSY` | command queue full | host re-sends identical frame later |
| `0x06` | `ERR_BAD_STATE` | command not allowed in the current state | host follows the lifecycle table; core state unchanged |
| `0x07` | `ERR_PATCH_TX_ACTIVE` | `PATCH_OPEN` while a transaction is open | host aborts or resets first |
| `0x08` | `ERR_PATCH_INCOMPLETE` | commit with names declared but not staged | host completes or aborts the transaction |
| `0x09` | `ERR_PATCH_HASH_MISMATCH` | computed patch hash differs from declared | transaction discarded; host may open a fresh transaction |
| `0x0A` | `ERR_UNKNOWN_NAME` | name not present in the negotiated name table | host corrects the name; staged state unchanged |
| `0x0B` | `ERR_DUPLICATE_NAME` | name staged twice with conflicting bytes, or duplicate in the declaration | host re-sends the intended single value |
| `0x0C` | `ERR_TX_TIMEOUT` | attempt to continue an expired transaction | fresh `PATCH_OPEN` required |
| `0x0D` | `ERR_PAYLOAD_LENGTH` | payload not the shape the command defines | host corrects the frame; core state unchanged |
| `0x0E` | `ERR_RENDER_BINDING` | render trigger's sound identity or declared noise-stream digest differs from the binding ([RENDER-TRIGGER.md](RENDER-TRIGGER.md)) | open clip discarded entire; host re-triggers from pass 1 |
| `0x0F` | `ERR_NOISE_STREAM` | noise-stream framing fault: wrong pass, non-contiguous offset, overrun, or pass 2 before pass 1 is complete ([RENDER-TRIGGER.md](RENDER-TRIGGER.md)) | open clip discarded entire; host re-triggers from pass 1 |

All errors are control-plane outcomes. They are distinct from, and define
nothing about, the numeric arithmetic error contract that DR-0008 ratifies;
the one place the numeric contract touches this document is the fatal
negotiation gate above, where mismatched contract versions must not interoperate.

## Live-note semantics are absent

This protocol has no note-on, note-off, gate, or continuous-control command,
allocates no code for them, and must not gain them within this one-shot
profile subset. The first product profile renders one four-second clip per
trigger ([`../VOICE-CONTRACT.md`](../VOICE-CONTRACT.md)); live-note semantics
belong to a later, separately named profile and would require a new decision
record and a new protocol version.
