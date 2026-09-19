# Explorer favorites, parameter locks, nearby variation

This layer implements issue #65 on top of the landed [explorer
session](EXPLORER-SESSION.md) and [explorer MVP](EXPLORER-MVP.md): portable
saved-sound favorites, locked parameters that survive variation, and
deterministic nearby variation with recorded lineage. It owns rich favorites,
locks, variation, lineage, portable import/export and migration refusal, as
reserved by EXPLORER-SESSION.md. It changes no DSP, parameter mapping, noise,
normalization, profile, arithmetic or timing rule, and adds nothing to the
render request surface.

The target remains `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`, default nebula,
four seconds at 44.1 kHz. All labels name software behavior only: no fidelity,
runtime qualification, hardware, transport or live-note claim is made.

## The favorite document

A favorite is one JSON document, schema-validated against
[`favorite-v1.schema.json`](schemas/favorite-v1.schema.json), which reuses the
render-artifact v1 definitions for references, profile, source, runtime and
parameter maps. Validation is structural in the schema and semantic in
`torchsynth_voice.favorites`; duplicate JSON keys, nonfinite numbers, unknown
fields, wrong versions and tampered identities fail. There are exactly two
kinds:

- **`capture`** — the complete record of one verified session selection. It
  stores the profile, source and runtime recorded provenance verbatim, the
  global `sound_index`, the exact `StoredArtifact.reference` (artifact ID and
  metadata digest), the recorded audio file digest/size/sample count, the full
  name-keyed normalized and physical maps with the recorded physical locks,
  and optional free-text notes. `parent_id` is null.
- **`variation`** — a derived patch specification. It records its
  `parent_id`, the inherited recorded provenance context, the global
  `sound_index`, the full perturbed physical patch and the final lock map, and
  the deterministic `{seed, amount}` specification that produced it. A
  variation stores **no** `artifact`, no audio digest and no
  `normalized_by_name`: variations perturb physical values only, and the
  normalized map is a recorded render output that this layer never
  reconstructs (EXPLORER-SESSION.md). Rendering a variation's full patch is
  not possible through the landed v1 render request, whose only patch delta is
  `locks_physical`; a variation therefore remains a portable, auditable patch
  specification, and nothing in this layer claims otherwise.

`favorite_id` (`fv1-` plus 64 hex digits) is the content identity of the
sound: the SHA-256 of the canonical encoding of the document minus
`favorite_id` and `notes`. Notes are annotations, not identity. Validation
recomputes the identity and refuses a mismatch, so an edited document cannot
pass as the favorite it claims to be.

## Capture

`favorites.capture(session, notes=...)` re-verifies the current selection
through `session.repeat()` — zero renderer and zero selection-RNG calls — and
reads the recorded inputs of the already-verified metadata bytes. It renders
nothing, reads nothing outside the verified selection, and never invents
values: every stored field is copied from the verified artifact record. A
capture stores the runtime as recorded provenance; it makes no admission or
qualification statement.

## Recall

`favorites.recall(session, path, store=...)` loads and validates the
document, then re-verifies the pinned artifact through the store and selects
it with `session.select`. It makes **zero renderer and zero selection-RNG
calls**: the exact saved artifact bytes are rehashed and reselected, so
repeating a recalled favorite reproduces its exact recorded patch and audio
under the environment that produced the pinned artifact. No audio is
re-rendered and no measurement is recomputed.

Before any store access, recall checks the environment against the document
and refuses incompatible drift:

- **profile change** — recorded profile name, pinned contract hash, sample
  rate, duration or expected sample count disagree with the current pinned
  contract;
- **source change** — recorded upstream commit, manifest digest or file
  hashes disagree with the current pinned contract;
- **name change** — the recorded physical patch is not exactly the current
  canonical inventory name set;
- **range change** — any recorded physical value falls outside the current
  inventory bounds for its name.

A variation favorite cannot be recalled as a selection (it pins no artifact);
recall refuses it with an actionable message while `favorites.load` still
validates it for inspection and further variation. Because every compatibility
check runs before selection, a refused recall — including a favorite whose
recorded patch, provenance or audio disagrees with its re-verified artifact —
always leaves the session's previous selection unchanged. The recorded runtime
is deliberately **not** compared at recall: it is provenance about the
rendering environment, not a validity gate, and hardware legitimately varies.

## Variation

`favorites.vary(parent, seed=, amount=, locks=, notes=...)` derives a nearby
variation deterministically:

- Locked parameters survive exactly. The final lock map is the parent's locks
  plus the requested additions; requested locks must be canonical inventory
  names, not already locked in the parent, and finite physical values within
  the current inventory range. Unknown, duplicate (CLI) and out-of-range
  locks are refused before any document is produced.
- Every unlocked parameter is perturbed by one seeded draw
  (`random.Random(seed)`, one `random()` call per unlocked name in sorted
  name order, symmetric within `amount` of the range span around the parent's
  recorded physical value) and clamped to the current inventory bounds. The
  same parent, seed, amount and locks always produce the same document, byte
  for byte, and the same `favorite_id`; a different seed produces a different
  patch. `amount = 0` degenerates to pure re-locking, which is the explicit
  lock-a-parameter variation path.
- The child records `parent_id`; chaining variations of variations
  accumulates locks and lineage. The child is schema-validated and
  identity-checked exactly like any favorite.

Only physical values participate in perturbation. This layer does not
implement the upstream normalized/physical curve mapping and does not claim
that a variation's audio exists until some qualified render actually produces
one.

## Import/export and storage

Favorites are portable by construction: the only locator field is the
store-relative artifact `ref`, and validation refuses absolute paths,
drive-letter forms and `..` components anywhere outside free-text notes.
Notes are never used for file resolution. `favorites.save` writes UTF-8 JSON
plus a trailing newline (sorted keys, `allow_nan=False` — the same document
always serializes to the same bytes) atomically: unique same-directory
temporary file, flush, fsync, `os.replace` commit point, cleanup limited to
the call's own temporary file. Like bookmarks, favorite documents must live
outside the **entire** store namespace, including staging and locks, and
symlinks cannot redirect them into it. `favorites.load` reads with
`O_NOFOLLOW`, requires an unaliased regular file, and refuses duplicate JSON
keys and nonfinite numbers.

A document declaring any other `schema_version` is refused with an explicit
migration message. There is no migration, no upgrade path and no
version negotiation in this layer; a future version must be a separate,
deliberate contract change.

## CLI

The explorer CLI (`tools/explore.py`) gains four commands over the same
session and store flags:

```sh
python3 tools/explore.py --store STORE --selected INDEX ARTIFACT_ID SHA256 \
  favorite-save FAVORITE --notes "optional text"
python3 tools/explore.py --store STORE --backend none \
  favorite-recall FAVORITE
python3 tools/explore.py --store STORE \
  favorite-vary PARENT DEST --seed 7 --amount 0.25 \
  --lock keyboard.midi_f0=60 --notes "locked pitch"
python3 tools/explore.py --store STORE favorite-inspect FAVORITE
```

`favorite-save` captures the current selection (`--selected` or `--bookmark`
initial state required). `favorite-recall` verifies and selects the saved
artifact; with `--backend none` it provably makes zero render calls.
`favorite-vary` needs no selection and writes the derived variation.
`favorite-inspect` validates and prints a document without touching a store.
Success prints one JSON envelope carrying the `favorite` document alongside
the usual session/admission/preview fields; errors print actionable stderr
and exit 2, and a failed favorite command never writes a favorite file nor
replaces a previous valid selection.

## Explicit exclusions

- **No protocol transport** and no `spec/protocol/` work.
- **No live-note or keyboard semantics**, no drum nebula, no holdout access:
  favorites for reserved indices 96–127 are refused at validation, and recall
  still passes through the session's identity gates.
- **No full-patch render requests.** The v1 render request surface is
  unchanged; only `locks_physical` deltas are renderable, and this layer adds
  no renderer, backend or adapter.
- **No migration** between document versions, and no discovery command that
  reads unknown identities.
- **No fidelity, qualification, hardware or playback claim.** Favorites bind
  recorded evidence; repeating one reproduces recorded bytes, and nothing
  here asserts how those bytes sound.
