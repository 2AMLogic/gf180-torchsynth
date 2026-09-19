# Explorer MVP (software-only)

This is the parent sound-explorer MVP: a local stdlib CLI over the landed
[explorer session](EXPLORER-SESSION.md) that generates, auditions, repeats,
inspects and saves one-shot sounds without a GPU. It owns the integration
layer only:

- the single real-render path through #15's public v1 adapter
  (`artifact_renderer.render_artifact`) with #12's qualified `DockerBackend`
  (`release-mkl-compatible-v1`, DR-0006);
- display-time #12 runtime admission derived from the ratified publication;
- a real local preview player invoked with an argument array;
- a clearly labeled fake mode for deterministic UI tests;
- one bounded committed smoke receipt (`sim/reference/explorer-smoke.json`).

Selection, verification, repeat, bookmarks and command dispatch remain owned
by [EXPLORER-SESSION](EXPLORER-SESSION.md); this document adds nothing to
those contracts. The normative source stays
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`; the product profile stays the
default nebula, four seconds, 44100 Hz one-shot.

## Explicit exclusions

- **No protocol transport.** `spec/protocol/` describes the later hardware
  lane; this MVP is software-only and implements none of it.
- **No #65 favorites/locks/variation/import-export/migrations.** The only
  persisted state is the minimal session bookmark v1.
- **No #66 transport backend work.** The backend seam is the landed
  `render_artifact` adapter; nothing transport-shaped is added.
- **No holdout access.** Indices 96-127 are refused before renderer or store
  access on every path, including explicit requests and RNG choices. There is
  no unseal UI.
- **No competing renderer or store bridge.** `explorer.StoreRenderer` calls
  #15's `render_artifact`; no other render path exists. Legacy
  `render_sound`/`cli.py` metadata is not valid storage evidence and is never
  published here.
- **No fidelity, hardware, RTL, silicon, live-note or keyboard claim.** All
  labels name software execution explicitly.

## Rendering path

```python
from torchsynth_voice.explorer import StoreRenderer, build_session
from torchsynth_voice.artifact_renderer import DockerBackend
from torchsynth_voice.storage import ArtifactStore

store = ArtifactStore("/physical/path/to/store")
renderer = StoreRenderer(store, DockerBackend(project_root="/clean/producer"),
                         producer_root="/clean/producer")
session, probe = build_session(store.root, backend="docker",
                               producer_root="/clean/producer")
```

`StoreRenderer(sound_index)` refuses reserved identities defensively, builds
`dict(request_template(project_root=...), fixture=fixture(sound_index))` — the
exact documented adapter pattern — and returns the published portable
reference. `render_artifact` validates the request (including the runtime
descriptor against the ratified publication) before backend access, and the
`DockerBackend` enforces the DR-0006 measured host scope, the qualified image
and the frozen clean producer checkout. A dirty producer root therefore fails
with an actionable refusal; pass `--producer-root` pointing at a clean
checkout to render. Batch size stays the qualified 32; no other execution
configuration is admitted.

`runtime_admission(inputs)` compares a verified artifact's recorded
provenance (`renderer_version`, restricted `runtime` descriptor, execution
configuration) with the ratified publication
`sim/reference/repeatability-runtime.json`. It is an honest display-time
provenance comparison, not a new qualification measurement: render-time
admission is enforced by the adapter and backend, and the session layer still
reports `runtime_qualification: "not-established"` for what it verifies
itself.

## Audition path

`explorer.PreviewPlayer` receives the just-verified `StoredArtifact`, derives
a lossy PCM16 WAV preview (`wav-pcm16-mono-44100hz`, quantized from the
validated `f32le-mono` samples) **outside** the artifact store, and invokes
the configured player command as an argument array with no shell
interpolation (`afplay` by default). Failures are actionable: a missing
player binary, a nonzero player exit and a preview directory inside the store
are all refusals that leave `last_error` and audition status visibly failed.
Normative samples and artifact hashes cannot change: the store is only read.
A successful local process is a preview outcome; it is not an acoustic or
hardware playback claim.

## Fake mode

`--backend fake` (or `build_session(backend="fake")`) uses
`FakeRenderer`/`FakePlayer`: deterministic, clearly labeled doubles over
synthetic protocol fixtures published by `publish_fake_artifact` (zero audio,
`fake-explorer-renderer`, synthetic runtime). Fake output is labeled
`fake-synthetic-fixtures-no-audio` in every CLI envelope and always reports
`runtime_admission.status` `"outside-admitted-profile"`. Fake-backed runs
prove UI/session behavior only; they are never render, admission, audition or
sound evidence. `--seed` makes `random` deterministic for UI tests;
`--fake-fail-render`/`--fake-fail-player` inject failures.

## CLI

```sh
python3 tools/explore.py --store STORE --backend docker \
  --producer-root /clean/producer --seed 7 random
python3 tools/explore.py --store STORE --backend docker \
  --producer-root /clean/producer --selected INDEX ARTIFACT_ID SHA256 save BOOKMARK
python3 tools/explore.py --store STORE --bookmark BOOKMARK repeat
python3 tools/explore.py --store STORE --backend none --bookmark BOOKMARK audition
```

Commands follow the session vocabulary: `next`, `random`, `request INDEX`,
`select INDEX ARTIFACT_ID SHA256`, `show`, `repeat`, `audition`,
`save PATH`, `recall PATH`, with `--selected`/`--bookmark` initial state.
Each invocation is one session; the bookmark carries selection across
processes. Success prints one JSON envelope: `backend` (honest label),
`session` (the verified session `show()` payload — profile, global sound
index, recorded runtime/provenance and the exact artifact reference/hash),
`runtime_admission` and `preview`. `--backend none` configures no renderer,
so recall/repeat/audition provably make zero render calls. Errors print
actionable stderr and exit 2, and a failed render, validation or playback
never replaces a previous valid selection or saves a false success: failed
playback stays visibly failed in that session even after a successful save.

## Smoke evidence

`sim/reference/explorer-smoke.json` records one bounded real run on the
admitted host: the exact commands, the rendered reference and hashes,
bookmark save/reload across a process restart, repeat with zero renderer
calls, and the real player outcome. Raw audio, stores and previews stay
uncommitted; the receipt is bounded software evidence with no sound
fidelity, admission of new hosts, holdout, or hardware playback claim.

```sh
python3 -m compileall -q src tests tools
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_explorer.py' -q
```
