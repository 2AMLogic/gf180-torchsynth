# Core/host framing, versioning, and negotiation

Normative frame envelope for host-to-core and core-to-host messages.
Protocol version 2 binds the numeric-dependent regions to the accepted
DR-0008 choice register (`spec/decision-records/0008-fixed-point-numeric-contract.md`,
Accepted by reviewed merge 2026-09-21; machine-readable register
`../reference/fixedpoint-choices-v1.json`, `dr_status: Accepted`, entries
C1–C10 `status: "accepted"`). Protocol version 1 carried these regions as
opaque byte strings or named placeholder widths pending #53; that gate has
closed, and version 2 replaces the placeholder policy with the bound
encodings defined here.

## Byte order and field order

- All multibyte integers are unsigned, little-endian, and contiguous.
- All fixed-point words are two's-complement, little-endian (least
  significant byte first), and contiguous.
- Fields appear in the order listed below; there is no alignment or padding.
- Variable-length regions are length-prefixed with a `u16` byte count followed
  by exactly that many bytes. No length-prefixed region may be interpreted by
  the framing layer; only the owning command defines (in a document in this
  directory) what its bytes mean.

## Frame envelope (protocol version 2)

| Offset | Size | Field | Meaning |
| --- | --- | --- | --- |
| 0 | 2 | `sync` | Constant `0x67 0xF1` (wire bytes, little-endian `u16` value `0xF167`). Receivers scan for `sync` to resynchronize. |
| 2 | 1 | `version` | Protocol version, currently `2`. |
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

## Numeric-contract version binding

The `numeric_contract_version` field is the 32-byte SHA-256 of the accepted
machine-readable DR-0008 choice register document
(`../reference/fixedpoint-choices-v1.json`). The register is the numeric
contract's identity: a peer's value identifies exactly which fixed-point
word formats its payloads use. Binding code must consume the register only
while `dr_status` is `Accepted` and the consumed entries carry
`status: "accepted"` (the consumer rule of DR-0008 Section 12); a register
that is not accepted is refused, never bound.

The pre-binding protocol version 1 reserved the ASCII value `unbound:#53`
for pre-#53 hosts. Under version 2 that value is stale: it identifies no
accepted register, and a core must refuse it exactly like any other
mismatched version (see negotiation rules).

## Bound numeric encodings

The regions protocol version 1 deferred are bound as follows. Each binding
names its accepted register authority; no value outside the accepted
register is bound here, and nothing here redefines the DR-0008 arithmetic
itself.

| Field | Bound encoding | Authority |
| --- | --- | --- |
| `numeric_contract_version` | 32-byte SHA-256 of the accepted register document | DR-0008 Sections 12/13 (Accepted 2026-09-21) |
| `patch_value` | exactly 4 bytes: 32-bit two's-complement Q10.21 word, little-endian (uniform host-entry word for every parameter; host real→word conversion rounds half-even and saturates at the Q10.21 rails) | DR-0008 C4, C6, C7 (accepted 2026-09-21) |
| `patch_hash` | exactly 32 bytes: SHA-256, domain-separated with the ASCII tag `gf180-torchsynth/patch-hash-v2`, a `0x00` byte, and the negotiated `numeric_contract_version` (see [PATCH-LOAD.md](PATCH-LOAD.md)) | DR-0008 Sections 12/13 (Accepted 2026-09-21) |
| `audio_sample` | exactly 3 bytes: 24-bit two's-complement Q2.21 word, little-endian, range [-4, +4), LSB 2^-21 (host real→word conversion rounds half-even and saturates at the Q2.21 rails) | DR-0008 C1, C6, C7 (accepted 2026-09-21) |

`patch_value` carries the uniform host-entry word; the core stores and
hashes it verbatim and never interprets it. Per-parameter physical
interpretation, and any narrower per-module internal formats, remain owned
by the fixed-model lanes — the name table's per-parameter `numeric_id` and
`width` columns stay reserved ([PATCH-LOAD.md](PATCH-LOAD.md)).

## Sample payload packing

An audio sample on the wire is the bound `audio_sample` word: 3 bytes,
little-endian Q2.21. Multi-sample payloads pack samples contiguously with no
alignment or padding, sample 0 first; a payload whose byte count is not a
multiple of 3 is malformed for any sample-bearing command. This is the
sample payload packing the audio transfer/streaming commands require; the
command codes themselves remain unallocated pending the architecture
decision record (issue #63), which owns the transfer/streaming command set.

## Still-opaque byte strings

Three regions remain opaque byte strings. Their deferring decisions did not
gate on #53 and have not landed; the producer chooses the bytes and the
consumer may only compare them for equality or relay them. No field of an
opaque byte string may be parsed, reordered, or normalized.

| Placeholder name | Encoded as | Deferred decision |
| --- | --- | --- |
| `sound_identity` | length-prefixed opaque byte string | global sound identity binding (issue #12/#88 qualification line) |
| `profile_id` | length-prefixed opaque byte string | product profile naming |
| `locks` | length-prefixed opaque byte string | device lock state; interpretation not defined by this subset |

## Versioning and negotiation

Session start is the only negotiation point. The host sends
`HELLO` (`command = 0x01`); the core answers `READY` (`0x02`) or a fatal
version error.

`HELLO` payload:

| Field | Encoding | Meaning |
| --- | --- | --- |
| `profile_id` | opaque byte string | requested product profile |
| `source_version` | opaque byte string | upstream source identity the host targets |
| `numeric_contract_version` | 32 bytes | SHA-256 of the accepted numeric register the host targets |
| `capabilities` | `u16` bitfield | requested capability bits, see below |

`READY` payload:

| Field | Encoding | Meaning |
| --- | --- | --- |
| `profile_id` | opaque byte string | core's active profile (echo or correction) |
| `numeric_contract_version` | 32 bytes | SHA-256 of the core's accepted numeric register |
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
| 2 | `render` | the `RENDER_TRIGGER`/`NOISE_STREAM` pass/digest binding of [RENDER-TRIGGER.md](RENDER-TRIGGER.md) is supported |

Negotiation rules:

- A `HELLO` whose header `version` differs from the core's supported version
  is answered with a fatal `ERR_PROTOCOL_VERSION` error response and the core
  returns to the closed state; no session exists until a new `HELLO`. This
  gate is what refuses stale peers: a protocol version 1 host (placeholder
  widths, `unbound:#53`) cannot open a session with a version 2 core.
- A `HELLO` whose `numeric_contract_version` differs from the core's bound
  value — including the stale `unbound:#53` marker — is answered with a
  fatal `ERR_PROTOCOL_VERSION` and the core returns to the closed state.
  Peers on different numeric contracts cannot interoperate: every payload
  word, sample, and patch hash is contract-bound.
- A `HELLO` received while a session is open is treated as an implicit reset
  followed by negotiation, never as a second concurrent session.
- The granted capability set is the intersection of requested and supported
  bits. A host that cannot proceed with the granted set closes the session
  (stops sending); there is no renegotiation mid-session.
- Sound identity and the patch hash are not negotiated: they travel in the
  patch transaction ([PATCH-LOAD.md](PATCH-LOAD.md)), and the patch hash is
  valid only under the negotiated numeric contract.

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
| `0x15` | `RENDER_TRIGGER` | host→core | [RENDER-TRIGGER.md](RENDER-TRIGGER.md) |
| `0x16` | `NOISE_STREAM` | host→core | [RENDER-TRIGGER.md](RENDER-TRIGGER.md) |
| `0x20` | `RESET` | host→core | [SESSION.md](SESSION.md) |
| `0xFE` | `ERROR` | core→host (as `kind = 0x03`) | [SESSION.md](SESSION.md) |

Codes `0x17–0x1F`, `0x21–0xFD`, and `0xFF` are reserved. The render
trigger and the host-fed noise stream (`0x15`/`0x16`) carry DR-0010's
pass/digest binding, which DR-0010 assigned to the transport lane
([RENDER-TRIGGER.md](RENDER-TRIGGER.md), issue #188). Render status,
trace/debug readout, normalization result, and audio output
transfer/streaming commands remain unallocated: their numeric wire formats
are bound (the sample payload packing above; the normalization gain word is
DR-0008 C9), but no landed record or issue allocates the commands that carry
them (DR-0010, the issue #63 record, did not). A receiver must answer any
unreserved but undefined code with `ERR_UNSUPPORTED_COMMAND`. There is no
live-note command and none may be allocated in this subset
([SESSION.md](SESSION.md)).
