# First clean development corpus (96 cases)

This is the issue #19 evidence record: one real, complete render of the fixed
development corpus — global indices 0–95 — through the landed #15 runner
(`tools/render_corpus.py`, `spec/CORPUS-RUNNER.md`) under the canonical runtime
profile DR-0006 `release-mkl-compatible-v1`. No tolerance or quality
conclusion is drawn from this run, and no holdout identity was rendered,
admitted, or inspected.

## Run identity

- Producer checkout: commit `bd8b35e47df24133a9bbfe41917c8dd35696f798`,
  clean (`dirty: false`), frozen before and re-checked after every worker call.
- Normative upstream source: TorchSynth `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`.
- Runtime: the ratified DR-0006 publication (lock
  `1966379f…81b25`, qualified immutable linux/amd64 image
  `sha256:b601c371…d58f`, one CPU, 6 GiB, all threads 1, `MKL_CBWR=COMPATIBLE`),
  launched on the measured Apple M5 / macOS 26.5.1 host with Docker server
  linux/arm64 29.7.2 under emulation.
- Manifest: checked-in `spec/reference/corpus-v0.json`
  (`141c7f05…6181`); default selection expanded to exactly 96 development
  identities; development mode refuses indices 96–127 before renderer or
  artifact-store access.
- Store: a new physical root that did not exist before the run
  (`/Users/joseph/dev/gf180-issue19-corpus-store`); the single-case smoke render
  used a separate scratch store and is not part of this corpus.
- Run ID: `9c443eed363e4874a07328d8ddadd095`.

## Accounting

- Counts (reconciled by the landed verifier against the full event journal):
  expected 96, observed 96, success 96, failure 0, attempt 96, retry 0,
  resume 0.
- All 96 audio payloads are exactly 176,400 finite little-endian float32
  samples (705,600 bytes each); 96 unique immutable artifacts.
- Render elapsed time 1483.6 s (per-attempt min/median/max 13.5/15.4/18.4 s);
  store 390 files, 71,820,533 bytes.
- Exact-byte verification without TorchSynth (`python3 -S … --verify`) passed
  against the recorded run-envelope digest
  `82406940…93e0df`.

## Evidence receipt and raw-byte custody

`sim/reference/development-corpus-first.json` is the bounded evidence receipt:
the exact commands and exit statuses, the embedded plan and stable index
documents, run/index/plan/envelope SHA-256 digests, producer and runtime
identity, storage and timing facts, and honest limits. It deliberately does not
embed the 1.4 MB run envelope (referenced by digest at
`runs/9c443eed…/summaries/000096.json`) and does not commit the ~69 MiB of raw
artifact bytes. The raw store is operator-retained at the recorded path; every
byte is authenticated by the per-artifact metadata hashes inside the index and
can be re-verified with the audit's store mode or fetched from that path.

## Audit and reproduction

```sh
# receipt-only (stdlib; internal consistency and digests)
python3 tools/audit_development_corpus.py
# with the retained raw store: landed read-only verifier plus audio re-scan
python3 tools/audit_development_corpus.py --store /physical/path/corpus-store
```

The audit independently re-derives the fixed denominator {0,…,95} from outside
the run: the landed runner/verifier reconciles a run against its own plan, but
neither re-establishes that the expected development set is exactly 0–95 —
including that a consistently re-counted 95- or 97-case index, a duplicate or
missing identity, or a holdout-claimed partition cannot pass.

Independent reproduction (#20's method, not this receipt): check out the
recorded producer commit `bd8b35e4…` in a fresh clone, allocate a new empty
store, and run

```sh
python3 tools/render_corpus.py --store /physical/path/new-store
```

Do not run from this later evidence-publication commit and overwrite
`project_git` with an older value; a fresh independent repeat uses a fresh
store and run.

## Limits

Single execution on one admitted host; not multi-host, sound-fidelity,
scalar-qualification, or hardware evidence. Holdout characterization is #21;
independent repeat and comparison is #20. Traces were explicitly not requested
(audio-only); absent traces are not measured trace coverage.
