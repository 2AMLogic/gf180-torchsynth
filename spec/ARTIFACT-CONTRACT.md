# Portable render artifacts and corpus indexes, version 1

This contract defines metadata, identity, and validation for the existing
[Voice profile](VOICE-CONTRACT.md). It does not change Voice behavior or select
a canonical runtime. The normative upstream commit remains
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`.

The machine contracts are
[render-artifact-v1.schema.json](schemas/render-artifact-v1.schema.json) and
[corpus-index-v1.schema.json](schemas/corpus-index-v1.schema.json).
The discriminator is the pair of `schema` and `schema_version`.
Existing renderer metadata containing only `schema_version: 1` is **not**
this format. Renderer migration, atomic publication, resume, and corpus
execution belong to downstream work.

## Required, optional, unavailable, and invalid

Every property in a schema's `required` array must be present.
Unknown properties are rejected. There are no implicit defaults.

| Form | Meaning |
| --- | --- |
| Required ordinary property | Known, correctly typed value; omission or null is invalid unless explicitly allowed |
| Optional property | `diagnostic_orders` on artifacts; `ref` on artifact references |
| `{"state":"available","value":…}` | Known inputs, audio facts, or captured traces |
| `{"state":"unavailable","reason":"reason-code"}` | Group was not obtained; carries no invented value |
| `{"state":"invalid","reason":"reason-code"}` | Group failed verification; carries no accepted value |

The required `inputs` group contains provenance and resolved render inputs.
If any required provenance is unavailable or invalid, mark the entire inputs
group accordingly, set `artifact_id` to null, and record a failure. Do not
substitute an empty version, zero digest, null runtime lock, or guessed commit.
A structurally well-formed hash is not proof that its preimage was measured.

`status: complete` requires available inputs/audio/traces and no failures.
`status: failed` requires at least one failure code. Warning codes may
accompany either state; warnings never erase failures. Array lengths are the
warning/failure counts. Empty requested/captured trace sets mean explicitly
that no traces were requested, not that unknown trace capture succeeded.

The default Python validators require complete records. Passing
`require_complete=False` permits inspection of explicit failed records,
while still rejecting malformed data and contradictory success claims. It
never promotes an unavailable group to available. In v1, invalid raw values
stay in private diagnostics outside the public artifact, not in `value`.

## Normative render identity inputs

`artifact_id = "ra1-" + sha256(domain || canonical_bytes(inputs.value))`,
with the exact ASCII domain `torchsynth-render-artifact-v1\n` (one LF).
**Every property and value in the available inputs object participates**:

| Input | Required contents |
| --- | --- |
| `profile` | Name, Voice contract file SHA-256, sample/control rates, duration, expected sample count, channels, dtype, nebula, normalization rule |
| `source` | Pinned commit, upstream manifest file SHA-256, name-keyed six normative source/nebula file SHA-256 values |
| `runtime` | Lock file SHA-256, OS, architecture, CPU, execution device, Python/Torch/Lightning/NumPy/TorchSynth versions |
| `project_git` | Project commit, dirty flag, tracked diff SHA-256, untracked-state SHA-256 |
| `renderer_version` | Renderer implementation/version identifier |
| `fixture` | Global sound index, equivalent upstream batch/slot/name, synth1B1 train/test designation |
| `execution` | Mode, execution batch size, reproducible flag |
| `parameters` | Complete normalized/physical maps and requested physical locks, all keyed by name |
| `noise` | Seed, selected canonical noise slot, selected stream byte SHA-256 |
| `trace_registry_version` | Registry version defining requested trace meanings/encodings |
| `requested_traces` | Ordered, unique trace names |

The ID excludes outputs, their storage locations/hashes, status, warnings,
failures, and diagnostic parameter order arrays. A changed normative input
produces a changed ID even if it happens to produce the same samples. Changing
output bytes requires new output integrity hashes, not a silently changed
input identity. A store must refuse conflicting outputs under one ID (#14).

`content_id` is an encoding helper, not a validation or qualification
gate. It can hash candidate inputs that a validator subsequently rejects.
Source/nebula files and the profile document are checked against the checked-in
pins by the semantic validator. No runtime lock is declared canonical by this
schema; an available lock hash must describe the actual execution environment.

### Canonical byte encoding

Version 1 uses a typed JSON tree to avoid implementation-specific decimal float
formatting. This is not RFC 8785/JCS. Parse ordinary JSON first: fractional or
exponent-form numbers are finite IEEE-754 binary64, rounded to nearest with
ties to even; integer-form numbers are exact integers. Object keys must be
unique Unicode scalar strings. Do not normalize Unicode.

Transform recursively:

| JSON value | Encoded tree |
| --- | --- |
| null | `["null"]` |
| boolean | `["bool",true]` or false |
| number | `["number","numerator","denominator"]` for its reduced exact rational value; denominator positive |
| string | `["string",value]` |
| array | `["array",[encoded elements in original order]]` |
| object | `["object",[[key,encoded value],…]]`, keys sorted by Unicode code point |

Numerator/denominator are decimal strings with no leading zeros, no plus sign,
and only a necessary minus sign. Thus 1 and 1.0 coincide, 0 and -0.0 coincide,
and true is distinct from 1. Arrays retain order; object insertion order is
irrelevant. Encode the tree as compact ASCII JSON without whitespace or a
trailing newline. Escape quote/backslash and JSON controls; use the standard
short escapes for backspace/tab/LF/form-feed/CR and lowercase `\uXXXX` for
other controls and all non-ASCII code points (surrogate pairs above U+FFFF).
Do not escape slash or printable ASCII.

Example input `{"z":0.5,"a":[true,null,"é"]}` yields these exact bytes:

```text
["object",[["a",["array",[["bool",true],["null"],["string","\u00e9"]]]],["z",["number","1","2"]]]]
```

JSON token NaN/Infinity, binary64 overflow, and unpaired Unicode surrogates
are rejected. Variable counts and identifiers require parsed integers,
excluding booleans and integral float tokens, and are bounded by 2^53−1.

## Hash scopes and portable references

All SHA-256 values are 64 lowercase hex digits. Git commits are 40 lowercase
hex digits. Hash scopes are deliberately distinct:

- Source manifest, Voice contract, runtime lock, source files, audio, and
  trace hashes cover exact file bytes. The noise hash covers the selected
  176,400-sample, little-endian float32 stream before modulation/VCA.
- `project_git.diff_sha256` covers exact `git diff --binary HEAD`
  output bytes (both staged and unstaged tracked changes).
- `project_git.untracked_sha256` covers `canonical_bytes` of a
  relative-path-to-file-SHA-256 map from `git ls-files --others --exclude-standard`.
  Producers must use repository-relative portable paths; unreadable files or
  unsupported nonregular entries invalidate provenance instead of disappearing.
  Ignored environment/build files do not participate. The clean state hashes
  empty diff bytes and the empty map; the validator checks dirty consistency.
- Corpus `corpus_manifest_sha256` identifies the preregistered input
  manifest bytes, not the generated index.
- An artifact reference's `sha256` covers exact stored artifact metadata
  bytes, including its content ID. This digest lives **in the containing index
  or scorecard**, never inside the hashed metadata itself. There is no self-hash
  cycle. The index may itself be hashed by an external manifest.

The shared reference boundary for scorecards (#18) and storage is an object
with `artifact_id` (opaque stable identifier) and `sha256` (64 lowercase hex
digits). Consumers can use this pair without importing the artifact validator.
V1 render IDs use `ra1-…`. Optional `ref` is a relative storage locator.
File references instead require `ref`, `sha256`, and `size_bytes`.

V1 permits only slash-separated path components beginning with an ASCII
letter, digit, underscore, or hyphen, followed by those characters or dots.
It deliberately supports no URI forms, percent escapes, backslashes, colons,
home expansion, empty components, or dot/dot-dot components. This rejects
absolute POSIX/Windows/UNC paths and traversal on every host. Public runtime
descriptors are restricted strings and warnings/reasons are portable codes,
not raw log messages that could embed host paths. A storage reader must also
enforce filesystem containment against symlinks; metadata validation does not
open references.

## Fixture identity versus execution

The fixture names the synth1B1 source draw independently of execution width:

- upstream batch index and slot are `divmod(sound_index, 128)`;
- upstream name is `synth1B1-{batch}-{slot}`;
- noise slot is `sound_index % 32`, and the noise seed is 13;
- train membership is `(sound_index // 1024) % 10 != 9`.

`canonical-batch` execution requires a positive multiple of 32,
`reproducible=true`, and CPU. It describes the reference fixture protocol,
not runtime qualification. `resolved-scalar` requires
`batch_size=1, reproducible=false` and consumes the resolved named patch
and selected noise stream. These executions receive different content IDs.
No batched/scalar sample equality or hardware batch requirement is implied.

Both parameter maps contain 78 entries with identical names. Names have
`module.parameter` syntax; physical locks must match entries in the
resolved physical map. Normalized values lie in [0,1], and all values must be
finite. Optional forward/randomization orders must each be permutations of
the map keys. They never define a patch. The validator checks syntax, counts,
and consistency; authentic inventory membership and normalized-to-physical
mapping require the pinned source/inventory checks in the producing pipeline.

## Corpus completeness and semantic validation

An index enumerates every expected case, including failed ones. Expected count
equals the case-array length; observed count equals the number of complete
cases. A complete index has all cases complete. Failed cases carry failure
codes and a null artifact reference. Case IDs, global sound indices, and
non-null artifact IDs must each be unique. Conflicting aliases are rejected.

`split` is development or holdout **within this preregistered corpus**;
it is independent of synth1B1's `is_train` field. V1 indexes allow one case
per global sound index; directed variations sharing a base draw need a
separately versioned case-identity extension.

JSON Schema covers field presence, types, scalar bounds/patterns, closed
objects, tagged group forms, and uniqueness of entire array elements.
The stdlib validator additionally enforces strict JSON reading, finite numbers,
integer/bool separation, current source/profile pins, derived fixture/noise
coordinates, execution rules, parameter map agreement/counts/locks, order
permutations, dirty-state consistency, content IDs, complete/failure semantics,
trace coverage, and audio sample/byte counts and index bounds. Corpus checks
add per-field identity uniqueness and expected/observed count relationships.
Supplying loaded artifacts additionally checks that every complete reference
resolves and its ID, fixture, profile, and runtime lock agree with the index.

Neither structural nor semantic metadata validation recomputes audio facts,
authenticates a renderer, proves runtime qualification, or verifies file bytes
that were not supplied. Storage consumers must separately read and hash every
referenced file; `verify_sha256(bytes, expected)` provides that check.
Validation succeeds with no TorchSynth, NumPy, PDK tools, or network access.

## API, fixtures, and evolution

From a repository checkout (the validators load the checked-in schemas/pins):

```python
from torchsynth_voice.artifacts import loads, validate_artifact, validate_corpus_index

artifact = loads(metadata_bytes)  # duplicate-key rejection needs this reader
validate_artifact(artifact)
index = loads(index_bytes)
validate_corpus_index(index, artifacts={artifact["artifact_id"]: artifact})
```

Passing an already parsed dictionary cannot recover duplicate keys a permissive
parser discarded. Do not use ordinary `json.loads` for untrusted metadata.

The committed fixtures under `tests/fixtures/artifacts/` are synthetic
validation evidence, not TorchSynth renders: runtime/renderer/patch/noise
identities are explicitly synthetic. The audio digest corresponds to generated
zero bytes; no audio file is committed. Tests exercise scalar execution, failure
states, negative mutations, identity sensitivity, and a canonical byte vector.
The one-case index fixture references the exact complete fixture bytes.

Run `python3 -m unittest discover -s tests -v` and
`python3 tools/check_contract.py`. No packaging, CI, renderer, or runtime
lock changes are required for this schema-only work.

Recorded schema evidence, 2026-09-18: 31 unittest tests passed (25 artifact
tests plus 6 existing tests) on Python 3.14.7 and 3.13.2; the focused suite
includes 39 rejection-fixture mutations. The contract checker reported
`contract manifests are internally consistent`. Both schemas passed
`Draft202012Validator.check_schema` and accepted their respective complete
fixtures using an independently available jsonschema 4.25.1 installation.
That additional check is not a project dependency. The test subprocess using
`python -S` validates the artifact without site packages or imports of Torch,
TorchSynth, NumPy, Lightning, or jsonschema. These results establish metadata
validation only, not render fidelity, runtime qualification, or hardware claims.

V1 is closed and immutable. Any added accepted property, changed requiredness,
meaning, identity input, canonical encoding, or validation rule that changes
the accepted document set requires a new explicit schema version and render
ID domain. Do not silently reinterpret old IDs or legacy metadata. A changed
patch, source/lock/version hash, execution, or requested trace set creates a
new ID even within a schema version; a changed corpus case set/split requires
a new corpus manifest/version. Changing synthesis behavior (source target,
nebula, arithmetic/noise policy, normalization, parameter meaning, or clip
timing) also requires a decision record and an appropriately named new profile
before implementation. Editorial clarification without changing semantics or
accepted documents may retain the schema version.
