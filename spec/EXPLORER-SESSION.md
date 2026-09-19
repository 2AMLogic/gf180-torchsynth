# Artifact-backed explorer session

This foundation selects and verifies complete local v1 artifacts through
[`ArtifactStore`](ARTIFACT-STORAGE.md). It uses the standard library, the
committed parameter inventory and the existing artifact validator. It does
not render audio, run a player, encode previews or admit a runtime. Parent
#64 owns those integrations, including #12 runtime admission and #15's public
render adapter. #65 owns rich favorites, locks, variation, lineage, portable
import/export and migrations.

The target remains `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`, default nebula,
four seconds at 44.1 kHz. This layer changes no DSP, parameter mapping, noise,
normalization, profile, arithmetic or timing rule.

## Session API

```python
from torchsynth_voice.explorer_session import ExplorerSession
from torchsynth_voice.storage import ArtifactStore

store = ArtifactStore("/physical/path/to/store")
session = ExplorerSession(store)
session.select(sound_index, reference)
display = session.show()
session.save("/physical/path/to/bookmark.json")
same = session.repeat()
restarted = ExplorerSession(store)
same_after_restart = restarted.recall("/physical/path/to/bookmark.json")
```

`sound_index` and `reference` above come from the caller's known selected
artifact request or containing index, never from speculative directory reads.
`reference` has exactly the published `StoredArtifact.reference` fields:
`artifact_id`, `sha256`, and `ref`. `ref` must equal
`artifacts/{artifact_id}/metadata.json`; arbitrary locator paths are refused.
The store root is supplied separately, so moving the unchanged store and
bookmark together does not change the reference.

| Operation | Behavior |
| --- | --- |
| `select(sound_index, reference)` | Verify a pinned existing artifact, then select it |
| `request(sound_index)` | Call the injected renderer with the allowed identity, independently verify its returned reference, then select |
| `next()` | Request the first allowed index initially, then the first greater than the selected index; exhaustion raises, never wraps |
| `random()` | Request one choice from the sorted allowed tuple, with replacement |
| `show()` | Reverify and return recorded profile/provenance, reference, resolved parameters/noise and current error/audition status |
| `repeat()` | Reverify and return the exact selection, without rendering, RNG or audition |
| `save(path)` | Reverify and atomically write the minimal bookmark |
| `recall(path)` | Strict-load the bookmark, gate its requested identity, reverify its exact reference, then select |
| `audition()` | Reverify immediately before invoking the injected player; record success only after it returns |

The constructor accepts `renderer`, `player`, `rng`, and `allowed_indices` as
keyword arguments. No backend is implicitly configured. The renderer callable
accepts one integer global `sound_index` and returns a portable reference or
raises an actionable exception. Its configuration belongs to the caller. The
player accepts the just-verified `StoredArtifact`, returns `None` on success,
and raises on failure. Returning successfully describes this injected call
only; it cannot prove an audio device played anything or grant runtime
qualification. No module names are dynamically imported from user input.

The injectable selection RNG has `choice(tuple_of_indices) -> int`. The default
is a session-owned `random.Random`, separate from global/DSP RNGs. Allowed
indices are copied, validated and sorted; input ordering does not define next
ordering. The default reads the committed `corpus-v0.json` development
partition (0–95). Explicit sets may include other nonreserved global indices,
but must be nonempty, unique integers in `[0, 2^53-1]`. Booleans, floats,
negative values, duplicates and reserved indices fail construction. The RNG's
returned choice is checked again, including membership in the allowed set.
Failed requests leave the current selection, and therefore next's position,
unchanged; an external renderer or RNG may already have advanced its own state.

Indices **96–127 remain sealed** regardless of configuration. There is no
unseal flag. Every explicit selection, renderer request, next/random candidate,
repeat and bookmark recall checks the requested identity before renderer or
artifact access. Recall must read the bookmark itself to learn this identity;
it never probes an artifact to infer it. The CLI constructs the store's root
directories via the normal store constructor but does not enumerate artifacts.
There is intentionally no discovery command that reads unknown identities.

## Verification and state

Every artifact use calls `ArtifactStore.verify(id, sha256=pinned_digest)` to
rehash metadata and every declared audio/trace payload, rejecting missing,
extra, truncated or corrupt files. Since `StoredArtifact` contains no parsed
metadata, the session separately reads `stored.path / "metadata.json"`, hashes
those **exact bytes** against the same pin, and uses strict `artifacts.loads`
and `validate_artifact`. Whitespace and the trailing newline participate in the
digest. Reserialized JSON cannot substitute for it. Both parameter maps must
match the inventory's exact 78-name set, and verified fixture identity must
match the original requested global sound index.

`Selection` is a frozen snapshot containing `sound_index`, `stored` and
`metadata_bytes`. Its `inputs` property parses a fresh copy of the recorded
inputs. It is useful for exact input comparison, not a capability to skip
verification. The session's read-only `selection` property retains the last
valid selection after an error; that snapshot is not a claim that its files
are still readable. Show/repeat/save/recall/audition all verify again. Repeat
and recall make zero renderer and selection-RNG calls. They reuse both
name-keyed normalized/physical maps, physical locks and the recorded selected
noise seed/slot/digest without conversion or reconstruction.

Show exposes `profile`, `sound_index`, and the verified `reference`, including
artifact ID and exact metadata hash. Its `recorded_provenance` contains the v1
`runtime` (environment/device/versions/lock), `execution` and
`renderer_version` fields, verbatim from inputs. V1 has no `backend` property.
Neither the current interpreter nor injected callable supplies provenance.
`artifact_bytes: "verified"` means structural validation and file-integrity
checks passed; `runtime_qualification: "not-established"` states this layer's
limit. Canonical-batch is a recorded execution mode, never a qualification.
Noise identity is a recorded digest; this layer does not reconstruct or hash
an unstored pre-modulation noise stream. It does not validate the DSP relation
between normalized/physical values or recompute recorded audio measurements.

Failures raise `SessionError` with the operation and underlying reason, and
populate `last_error`. A successful retry of that same action clears its
error; unrelated successes preserve it. Audition status is separate, includes
the attempted artifact ID/digest/global index, and changes only on another
audition attempt. Thus a failed playback followed by a successful save remains
visibly failed. A prior successful attempt on another selection is labeled
with that prior artifact's identity. A new session has no audition history;
recalling a bookmark never implies a successful audition. This is local
session status, not a persisted evidence record.

## Minimal bookmark v1

The complete object has exactly these fields (schematic values below):

```json
{
  "schema_version": 1,
  "sound_index": 6,
  "artifact": {
    "artifact_id": "ra1-<64 lowercase hex digits>",
    "sha256": "<64 lowercase hex digits>",
    "ref": "artifacts/ra1-<same artifact ID digits>/metadata.json"
  }
}
```

Unknown fields/versions, duplicate JSON keys, malformed numbers/strings,
invalid references, disallowed identities and mismatched/stale records fail.
No patch, provenance or audition result is duplicated into the bookmark.
Full inputs come from the pinned artifact after restart.

Bookmarks must be outside the **entire selected store namespace**, including
completed artifacts, staging and locks; symlinks cannot redirect a bookmark
into it. The destination parent directory must already exist. Save writes
UTF-8 JSON plus a newline to a unique same-directory temporary file, flushes
and fsyncs that file, then uses `os.replace` as its commit point. Failures
before replacement preserve prior bookmark bytes and the selected artifact.
Cleanup removes only this call's temporary file, never other callers' files.
Successful replacement is not rolled back. A process death may leave its own
temporary file; no global scavenger runs.

This is an atomic local-filesystem/cooperative-caller protocol. Completed
artifact directories are immutable; callers must not rename or mutate them
concurrently. The session is not thread-safe, and concurrent bookmark writers
use last-successful-replacement semantics. It does not sandbox hostile peers
with the same filesystem privileges, qualify network filesystems/Windows, or
promise directory-fsync/power-loss durability. Copying the reference or saving
it cannot authenticate synthetic or forged producer assertions.

## Commands from a source checkout

No site packages or packaging edits are required:

```sh
PYTHONPATH=src python3 -S -m torchsynth_voice.explorer_commands --help
```

For an existing reference, substitute its recorded global identity, full
artifact ID and exact metadata digest. These shell variables are caller
inputs, not values derived by this CLI from unknown artifact contents:

```sh
PYTHONPATH=src python3 -S -m torchsynth_voice.explorer_commands \
  --store "$STORE" select "$INDEX" "$ARTIFACT_ID" "$METADATA_SHA256"
PYTHONPATH=src python3 -S -m torchsynth_voice.explorer_commands \
  --store "$STORE" --selected "$INDEX" "$ARTIFACT_ID" "$METADATA_SHA256" show
PYTHONPATH=src python3 -S -m torchsynth_voice.explorer_commands \
  --store "$STORE" --selected "$INDEX" "$ARTIFACT_ID" "$METADATA_SHA256" save "$BOOKMARK"
PYTHONPATH=src python3 -S -m torchsynth_voice.explorer_commands \
  --store "$STORE" recall "$BOOKMARK"
PYTHONPATH=src python3 -S -m torchsynth_voice.explorer_commands \
  --store "$STORE" --bookmark "$BOOKMARK" repeat
```

Each invocation creates a new session. Supply `--selected` or `--bookmark`
before commands needing a current selection; these options are mutually
exclusive and precede the subcommand. Successful commands print verified JSON.
Errors print actionable stderr and exit 2, with no success JSON. Standalone
`request INDEX`, `next`, `random` and `audition` explicitly report missing
renderer/player capabilities. There is no `--fake` path.

Parent code calls `explorer_commands.dispatch(session, argv)` with its
configured session and ordinary command arguments, e.g. `["next"]`,
`["request", "6"]`, `["audition"]`, `["save", path]`. It returns the same
verified display dictionary or raises `SessionError`; it does not print.
Dispatch verifies again for its final show response.

## Test scope

`tests/test_explorer_session.py` and `tests/test_explorer_commands.py` use real
temporary `ArtifactStore.publish/verify` operations, deterministic injected
renderer/player/RNG doubles and failure counters. They adapt the existing
synthetic storage fixture only inside temporary directories: canonical
inventory names, development identity, matching upstream coordinates/train
flag/noise slot, corresponding locks/orders, content ID and exact metadata
digest. Values remain deliberately synthetic; 705,600 zero audio bytes and
tiny trace payloads are protocol fixtures, not rendered sound evidence.

The tests cover exact reuse and unchanged artifact files, full payload rehash,
canonical-name and pre-access holdout gates, a separate metadata-read mutation,
atomic pre-commit failures, persistent audition failures, and useful separate
stdlib CLI invocations. No generated artifact or capability evidence is
committed. No actual TorchSynth render, runtime qualification, actual player,
preview, live keyboard, acoustic fidelity, hardware or physical validation is
claimed.

```sh
PYTHONPATH=src python3 -S -m unittest discover -s tests -p 'test_explorer*.py' -q
TORCHSYNTH_ROOT=/tmp/torchsynth-review-20260918 PYTHONPATH=src \
  python3 -S -m unittest discover -s tests -q
TORCHSYNTH_ROOT=/tmp/torchsynth-review-20260918 \
  python3 tools/check_contract.py --require-git-commit
```
