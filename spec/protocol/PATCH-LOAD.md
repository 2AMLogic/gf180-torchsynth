# Name-keyed patch load and the canonical name table

Normative patch load path. Parameter values are carried as opaque byte
strings with placeholder widths ([FRAMING.md](FRAMING.md)); this document
defines only the name routing, transaction structure, and the canonical name
table. No numeric encoding, width, or scaling is fixed here — that is #53.

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
| `value` | opaque byte string (`patch_value` placeholder) | the parameter's value bytes; width and encoding are #53's |

`PATCH_COMMIT` (`0x13`) payload: one opaque byte string, the declared
`patch_hash` over the transaction's values.

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
  length. Pre-#53 the digest algorithm is a named stand-in (SHA-256, marked as
  a placeholder in the mock harness); the final binding of algorithm, domain
  separation, and width is #53's. Consumers treat the result as an opaque
  byte string.
- Names must be declared before their value arrives; a `PATCH_VALUE` for an
  undeclared name is `ERR_UNKNOWN_NAME`. A name declared twice, or a value
  re-staged with different bytes, is `ERR_DUPLICATE_NAME`.
- `transaction_id` must be unique per attempt within a session. Reusing a
  finished transaction's id is `ERR_BAD_SEQUENCE` semantics: nothing is
  applied.
- The core is the sole writer of patch state; the host has no read-back of
  values in this subset. Verification is by hash at commit time.
