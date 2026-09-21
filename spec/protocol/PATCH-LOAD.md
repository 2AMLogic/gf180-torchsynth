# Name-keyed patch load and the canonical name table

Normative patch load path. Parameter values are carried as bound 32-bit
Q10.21 words and the patch hash is a bound, domain-separated SHA-256
([FRAMING.md](FRAMING.md)); this document defines the name routing,
transaction structure, and the canonical name table. Per-parameter physical
interpretation of the value word stays owned by the fixed-model lanes; no
per-parameter width table exists on the wire.

## Canonical name table

The canonical parameter names are the 78 names of
[`../reference/parameter-inventory-v1.json`](../reference/parameter-inventory-v1.json),
each of the form `<module>.<parameter>` (including upstream `->` in
modulation route names). That inventory is the source of truth for the name
set; this protocol does not redefine, abbreviate, or reorder it. Persisting
and loading patches by name follows the inventory's own rule that display
order is not an interchange format.

The protocol carries a **name table reference**, not a copy of the table:
during `HELLO` negotiation the host and core identify the table by the
SHA-256 of the canonical inventory document, carried as an opaque byte
string. A core that cannot identify the host's table must not accept
`PATCH_NAME` frames for names it cannot resolve; it answers
`ERR_UNKNOWN_NAME`. If #53 or a later decision record introduces compact
numeric parameter IDs, they must be defined as a versioned mapping **through**
this table — the table structure below reserves that mapping as a
numeric-gated column and no wire code in this subset depends on it.

Versioned name table document structure (the structure a derived table must
follow when a core materializes one; the committed inventory itself remains
the pinned source):

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | this structure's version, currently 1 |
| `table_version` | integer | monotonically increased when the mapped name set changes |
| `source` | object | `inventory_sha256`, `upstream_commit`, and inventory `schema_version` it was derived from |
| `routing` | string | `name` in this subset; `numeric` is reserved for the post-#53 mapping |
| `entries` | array | one entry per canonical name: `{"name": "<module>.<parameter>"}` in this subset; `numeric_id` and `width` keys are reserved and must be absent until their decision records land |

## Patch load transaction

All frames below are commands from the host; the core answers each with an
empty-payload success response (`kind = 0x02`, same `command` and
`sequence`) unless an error response is defined.

`PATCH_OPEN` (`0x10`) payload:

| Field | Encoding | Meaning |
| --- | --- | --- |
| `transaction_id` | opaque byte string, 1–16 bytes | host-chosen id keying the whole transaction |
| `sound_identity` | opaque byte string (`sound_identity` placeholder) | the global sound identity this patch belongs to |
| `name_table_sha256` | opaque byte string | the table reference agreed at negotiation |
| `declared_name_count` | `u16` | exact number of names the patch declares |

`PATCH_NAME` (`0x11`) payload: one opaque-byte-string name (UTF-8 canonical
name, length-prefixed). Declares the name; its value arrives separately.

`PATCH_VALUE` (`0x12`) payload:

| Field | Encoding | Meaning |
| --- | --- | --- |
| `name` | opaque byte string | must equal a declared name |
| `value` | exactly 4 bytes: 32-bit two's-complement Q10.21 word, little-endian | the parameter's value word (uniform host-entry encoding; DR-0008 C4, C6, C7) |

A `value` region of any length other than 4 is `ERR_PAYLOAD_LENGTH`; the
core never applies it.

`PATCH_COMMIT` (`0x13`) payload: exactly 32 bytes, the declared `patch_hash`
over the transaction's values under the negotiated numeric contract. Any
other length is `ERR_PAYLOAD_LENGTH`.

`PATCH_ABORT` (`0x14`) payload: empty.

Transaction rules:

- A transaction is staged, never applied incrementally. The core applies the
  whole patch atomically at `PATCH_COMMIT` and only then: every declared name
  must have been declared once, staged exactly once, and the computed patch
  hash must equal the declared hash. Any failure discards the staged state in
  full and returns the core to `ready`; a partial patch can never become
  active, and a retry cannot mix values from different attempts because each
  attempt keys on a fresh `transaction_id` and staging is keyed by it.
- The patch hash is computed over the concatenation of staged entries in
  sorted canonical-name order: for each name, its UTF-8 bytes, a single `0x00`
  separator byte, its value bytes, then the two-byte little-endian value
  length. The digest is the bound `patch_hash`: SHA-256 over the ASCII domain
  tag `gf180-torchsynth/patch-hash-v2`, a single `0x00` byte, the negotiated
  `numeric_contract_version` (32 bytes), and that staged concatenation
  ([FRAMING.md](FRAMING.md)). The domain separation binds every patch hash to
  the numeric contract under which the value words were encoded: a hash
  computed under one contract can never validate a transaction under another.
  The 32-byte digest is carried verbatim in `PATCH_COMMIT`.
- Names must be declared before their value arrives; a `PATCH_VALUE` for an
  undeclared name is `ERR_UNKNOWN_NAME`. A name declared twice, or a value
  re-staged with different bytes, is `ERR_DUPLICATE_NAME`.
- `transaction_id` must be unique per attempt within a session. Reusing a
  finished transaction's id is `ERR_BAD_SEQUENCE` semantics: nothing is
  applied.
- The core is the sole writer of patch state; the host has no read-back of
  values in this subset. Verification is by hash at commit time.
