# Core/host framing, versioning, and negotiation

Normative frame envelope for host-to-core and core-to-host messages. This
document is arithmetic-independent: it fixes control-plane structure only.
Final numeric-contract binding of the header and of hash computation waits on
#53; until then every numeric-dependent region is an opaque byte string or an
explicitly named placeholder width.

## Byte order and field order

- All multibyte integers are unsigned, little-endian, and contiguous.
- Fields appear in the order listed below; there is no alignment or padding.
- Variable-length regions are length-prefixed with a `u16` byte count followed
  by exactly that many bytes. No length-prefixed region may be interpreted by
  the framing layer; only the owning command defines (in a document in this
  directory) what its bytes mean.

## Frame envelope (protocol version 1)

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | 2 | `sync` | Constant `0x67 0xF1` (wire bytes, little-endian `u16` value `0xF167`). Receivers scan for `sync` to resynchronize. |
| 2 | 1 | `version` | Protocol version, currently `1`. |
| 3 | 1 | `kind` | `0x01` command (host→core), `0x02` response (core→host), `0x03` error response (core→host). |
| 4 | 1 | `command` | Command code; see the registry below. Error responses echo the command code that failed. |
| 5 | 2 | `sequence` | `u16` host-assigned session-scoped counter; responses and error responses echo it. |
| 7 | 2 | `length` | `u16` payload byte count, 0–65535. |
| 9 | `length` | `payload` | Command-specific; opaque to framing. |
| 9+`length` | 2 | `crc` | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflect, xorout 0) over bytes 2 through 9+`length` (everything after `sync`). |

A frame is well-formed only if `sync` matches, `version` is supported, `kind`
is known, `length` is consistent with the received byte count, and the CRC
matches. Receivers must treat any violation as a bad frame
([SESSION.md](SESSION.md) error recovery) and must never partially apply a
frame's effect.

## Opaque byte strings

Where this protocol suite says a field is an **opaque byte string**, the
producer chooses the bytes and the consumer may only compare them for equality
or relay them. This is how profile identity, source version, the
numeric-contract version, the patch hash, the sound identity, and the patch
transaction id travel before #53 fixes their internal structure. Comparing
opaque byte strings for equality is the only permitted operation; no field of
an opaque byte string may be parsed, reordered, or normalized.

## Named placeholder widths

Regions whose final width or encoding is numeric-dependent are declared as
placeholder widths and named as such. The placeholders defined by this suite:

| Placeholder name | Encoded as | Deferred decision |
| --- | --- | --- |
| `patch_value` | length-prefixed opaque byte string | per-parameter fixed-point width, scaling, and encoding (#53) |
| `numeric_contract_version` | length-prefixed opaque byte string | final numeric-contract version binding (#53) |
| `patch_hash` | length-prefixed opaque byte string | final hash computation binding (#53); the mock harness uses a named stand-in digest only |
| `sound_identity` | length-prefixed opaque byte string | global sound identity binding (issue #12/#88 qualification line) |
| `profile_id` | length-prefixed opaque byte string | product profile naming |
| `locks` | length-prefixed opaque byte string | device lock state; interpretation not defined by this subset |

A placeholder may not be given a concrete numeric interpretation anywhere in
`spec/` or `src/` until the deferring decision record lands.

## Versioning and negotiation

Session start is the only negotiation point. The host sends
`HELLO` (`command = 0x01`); the core answers `READY` (`0x02`) or a fatal
version error.

`HELLO` payload:

| Field | Encoding | Meaning |
| --- | --- | --- |
| `profile_id` | opaque byte string | requested product profile |
| `source_version` | opaque byte string | upstream source identity the host targets |
| `numeric_contract_version` | opaque byte string | host's numeric-contract expectation; the value `unbound:#53` (ASCII) is reserved for pre-#53 hosts |
| `capabilities` | `u16` bitfield | requested capability bits, see below |

`READY` payload:

| Field | Encoding | Meaning |
| --- | --- | --- |
| `profile_id` | opaque byte string | core's active profile (echo or correction) |
| `numeric_contract_version` | opaque byte string | core's numeric-contract identity, `unbound:#53` pre-#53 |
| `locks` | opaque byte string (`locks` placeholder) | core lock state, carried verbatim; its semantics are not defined by this subset |
| `capabilities` | `u16` bitfield | granted capability bits: requested AND supported |
| `max_payload` | `u16` | largest payload the core will accept, ≤ 65535 |
| `rx_queue_depth` | `u8` | command queue depth for backpressure accounting |
| `patch_timeout_ms` | `u16` | core-side open-transaction inactivity timeout |

Capability bits (subset-defined; all other bits reserved and must be sent as
zero, and a core must clear reserved bits it grants):

| Bit | Name | Meaning |
| --- | --- | --- |
| 0 | `name_keyed_patch_load` | the PATCH transaction of [PATCH-LOAD.md](PATCH-LOAD.md) is supported |
| 1 | `reset` | the RESET command of [SESSION.md](SESSION.md) is supported |

Negotiation rules:

- A `HELLO` whose header `version` differs from the core's supported version
  is answered with a fatal `ERR_PROTOCOL_VERSION` error response and the core
  returns to the closed state; no session exists until a new `HELLO`.
- A `HELLO` received while a session is open is treated as an implicit reset
  followed by negotiation, never as a second concurrent session.
- The granted capability set is the intersection of requested and supported
  bits. A host that cannot proceed with the granted set closes the session
  (stops sending); there is no renegotiation mid-session.
- Sound identity and the patch hash are not negotiated: they travel opaquely
  in the patch transaction ([PATCH-LOAD.md](PATCH-LOAD.md)).

## Command registry (subset allocation)

| Code | Name | Direction | Defined in |
| --- | --- | --- | --- |
| `0x01` | `HELLO` | host→core | this document |
| `0x02` | `READY` | core→host | this document |
| `0x10` | `PATCH_OPEN` | host→core | [PATCH-LOAD.md](PATCH-LOAD.md) |
| `0x11` | `PATCH_NAME` | host→core | [PATCH-LOAD.md](PATCH-LOAD.md) |
| `0x12` | `PATCH_VALUE` | host→core | [PATCH-LOAD.md](PATCH-LOAD.md) |
| `0x13` | `PATCH_COMMIT` | host→core | [PATCH-LOAD.md](PATCH-LOAD.md) |
| `0x14` | `PATCH_ABORT` | host→core | [PATCH-LOAD.md](PATCH-LOAD.md) |
| `0x20` | `RESET` | host→core | [SESSION.md](SESSION.md) |
| `0xFE` | `ERROR` | core→host (as `kind = 0x03`) | [SESSION.md](SESSION.md) |

Codes `0x15–0x1F`, `0x21–0xFD`, and `0xFF` are reserved. Render trigger and
status, trace/debug readout, normalization result, and audio
transfer/streaming commands are deliberately unallocated until #53 fixes the
numeric wire formats they require. A receiver must answer any unreserved but
undefined code with `ERR_UNSUPPORTED_COMMAND`. There is no live-note command
and none may be allocated in this subset ([SESSION.md](SESSION.md)).
