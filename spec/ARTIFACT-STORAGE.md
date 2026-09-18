# Atomic reference artifact storage

`torchsynth_voice.storage.ArtifactStore` stores the complete v1 documents
defined by [ARTIFACT-CONTRACT.md](ARTIFACT-CONTRACT.md). It uses only the Python
standard library and imports no renderer, Torch, NumPy, or PDK tools. It is a
library primitive for one artifact; corpus orchestration and renderer/CLI
integration are separate work.

The normative TorchSynth commit remains
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`. The profile remains one four-second,
44.1 kHz, default-nebula clip. Both validated `canonical-batch` fixture execution
and `resolved-scalar` execution are accepted, with distinct input identities.
The hardware product is one sound per trigger; reference batching is a
qualification fixture protocol, not a hardware batching requirement.

## Layout and API

```text
store/
  .publish.lock                  persistent advisory lock; never unlink in use
  .staging/render-<random>/      private, undiscoverable snapshots
  artifacts/ra1-<input-hash>/
    metadata.json               reserved store metadata filename
    <declared audio path>
    <every declared trace path>
```

The caller supplies a directory containing exactly `metadata.json` and the
files it declares. Audio/trace references are relative to that directory.
Only necessary parent directories are allowed; extra empty directories also
fail. Duplicate references, a data reference to `metadata.json`, and paths
that are both a file and a directory are rejected.

```python
from pathlib import Path
from torchsynth_voice.storage import ArtifactStore

# These paths must use physical directory components (no symlinks).
store = ArtifactStore(Path("reference-store"))
stored = store.publish(Path("finished-render"))
reference = stored.reference
# {"artifact_id": "ra1-...", "sha256": "...",
#  "ref": "artifacts/ra1-.../metadata.json"}
checked = store.verify(reference["artifact_id"], sha256=reference["sha256"])
completed = store.discover()
```

`StoredArtifact` contains `artifact_id`, local `path`, exact metadata `sha256`,
and `resumed`. `resumed` is true only when `publish` reuses an existing exact
match. `verify` always reads all files; `discover` returns a sorted tuple of
verified completed artifacts and raises on invalid entries instead of silently
omitting them. Discovery can exclude a publication concurrent with its initial
directory listing; the next call will include it.

The portable `reference.ref` is relative to the **store root**. The reference
digest covers the exact stored metadata bytes, including whitespace and the
trailing newline, never `canonical_bytes(metadata)`. Retain the reference in a
containing index or other trusted record and pass its digest to `verify` when
checking that exact metadata revision. Without that digest, validation checks
the current self-consistent files; it cannot authenticate a malicious rewrite
of both metadata and data. A future scorecard bridge must explicitly map
`reference.artifact_id` to scorecard `artifact.identity` and preserve `sha256`;
the storage reference is not itself a scorecard row.

## Publication, collision, and interruption

1. Create a unique private snapshot under `.staging` on the store filesystem.
2. Strict-load metadata with `loads`, then call `validate_artifact` with its
   default complete-only requirement. Compute identity with the landed
   `content_id(inputs.value)` API. Schema validation precedes file operations
   driven by declared paths.
3. Read every audio/trace file, check exact size and `verify_sha256`, and copy
   the validated bytes. Re-read and verify the complete snapshot before
   publication. The caller's source directory is never changed or removed.
4. Hold the exclusive OS lock across existence check, existing-artifact
   validation/comparison, and rename. A new artifact becomes visible by one
   directory rename into `artifacts`; no partially copied directory is placed
   there. File and directory writes are flushed with `fsync`.
5. If the target exists, fully revalidate it. Identical relative file/hash maps,
   including exact metadata bytes, return `resumed=True` without rewriting any
   completed file or its modification timestamp. Different valid content raises
   `CollisionError`; corrupt existing content raises `ValidationError` (or its
   `StorageError` subclass). Neither path overwrites the existing artifact.

The ID addresses **canonical inputs**, not output bytes. Even a metadata-only
whitespace change under an existing ID is a collision. `CollisionError`
provides `artifact_id`, `existing_sha256`, `candidate_sha256` (the two metadata
hashes), and `differences`, a relative-path mapping to `(existing, candidate)`
hash pairs. Its message includes the differing hashes; a missing counterpart
is explicitly identified. A changed output hash cannot silently select a new
input identity or be accepted as an exact resume.

Every exception, including `KeyboardInterrupt`, removes only this call's private
snapshot. Process death before rename may leave an orphan under `.staging`,
which discovery ignores and subsequent publications leave alone. There is no
automatic global cleanup that could delete another writer's work. Remove
abandoned snapshots administratively only after their owners are known to have
stopped. Never delete the persistent lock file while publishers may be running.
Death after rename can leave a complete artifact even if the caller received
no success result; retry validates and resumes it.

## Filesystem boundary

This implementation requires a local POSIX filesystem with directory-relative
opens, `O_NOFOLLOW`, `flock`, atomic same-filesystem rename, and `fsync` support.
Publication is serialized across cooperating processes using a stable lock
inode; readers do not need the lock because completed artifacts are immutable
under this protocol. First lock creation is exclusive, with other publishers
opening the existing file. An OS-released lock survives writer death without
stale-lock recovery or a daemon.

Path syntax comes from the existing schema. Filesystem validation separately
walks directory components through directory file descriptors with
`O_NOFOLLOW`, including the supplied root and its ancestors. It rejects all
symlinks, not only ones whose targets happen to escape today. Files must be
regular and have one link; hardlink aliases, FIFOs, and devices are refused.
Opens use `O_NONBLOCK` so an unexpected FIFO cannot hang validation. Reads
check for size/timestamp changes, and publication revalidates the private copy.
Use an explicitly selected physical base on hosts where system temporary paths
are symlink aliases; do not resolve untrusted artifact references to bypass
these checks.

The store namespace must be controlled by cooperating callers: manual deletion,
renaming, mounting, or hostile mutation of its directories while operations run
is outside this protocol. It is not a sandbox against another process with the
same filesystem privileges. Completed paths are local conveniences, not a
capability to bypass verification. Network filesystems, Windows, and power-loss
durability have not been qualified. The tests establish process-interruption
behavior, not physical crash recovery. Data is held one file at a time in
memory; metadata and its parsed representation are also retained.

## Verification evidence

Recorded 2026-09-18 on macOS, Python 3.14.7:

| Check | Observed result |
| --- | --- |
| `python3 -m unittest discover -s tests -v`, with `TORCHSYNTH_ROOT` pointing to the verified pinned checkout | 91 tests passed; no skips |
| `python3 tools/check_contract.py` | `contract manifests are internally consistent` |
| `python3 -S -m unittest discover -s tests -p test_storage.py -v` | All 16 storage tests passed without site packages |
| Concurrent-writer test repeated 20 times | All 20 passed: 40 writer pairs / 80 spawned writers across identical and conflicting cases |
| `python3 -m compileall -q src tests tools` | Exit 0 |
| Ruff 0.12.7 format check and lint on the two new Python files | Both passed |

The focused suite covers exact reference hashes, unchanged resume bytes/inodes/
timestamps, scalar versus fixture identities, metadata/output collisions,
strict/schema-invalid JSON, missing/extra/truncated/corrupt files, reserved and
aliased paths, symlink components, hardlinks, FIFOs, scoped cleanup, and corrupt
existing content. Two spawned writers pause with validated private snapshots;
the parent observes zero completed artifacts before releasing them to compete.
Identical writers produce one new artifact and one resume; conflicting writers
produce one new artifact and one collision. Actual subprocess exits during
copying and immediately before rename leave only undiscoverable staging data;
an injected interruption after rename yields a verifiable artifact on retry.

Fixtures are generated in temporary directories from the existing synthetic
metadata fixture: 705,600 zero audio bytes (the contract's full 176,400 samples)
and two tiny synthetic traces. No audio is committed and no actual renderer is
run. These results prove the storage protocol only; they do not establish sound
fidelity, runtime qualification, synthesis, layout, signoff, or hardware playback.
