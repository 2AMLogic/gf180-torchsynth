# Independent byte-repeat of the development corpus (96 cases)

This is the issue #20 evidence record: a second, independent render of the
fixed development corpus — global indices 0–95 — compared byte-for-byte
against the landed #19 first run. The second execution used a fresh clone of
the same frozen producer commit, a genuinely separate empty store, and the
sole #15 runner (`tools/render_corpus.py`, `spec/CORPUS-RUNNER.md`). Every
one of the 96 cases repeats exactly: the corpus index documents are
byte-identical, and per-case metadata and audio bytes match with zero
mismatches. No tolerance or quality conclusion is drawn, and no holdout
identity was rendered, admitted, or inspected.

## Run identity

- Producer checkout: commit `bd8b35e47df24133a9bbfe41917c8dd35696f798`,
  clean (`dirty: false`) — the same frozen revision as #19's first run, taken
  as a fresh `--no-hardlinks` clone with detached HEAD, so `project_git`
  identity is unchanged and the repeat does not make identity drift
  inevitable by moving the producer. The backend re-verified the frozen
  identity before and after every worker call.
- Normative upstream source: TorchSynth `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`.
- Runtime: the ratified DR-0006 publication
  (`release-mkl-compatible-v1`, qualified immutable linux/amd64 image
  `sha256:b601c371…d58f`, one CPU, 6 GiB, all threads 1, `MKL_CBWR=COMPATIBLE`)
  on the same measured Apple M5 / macOS 26.5.1 host (Docker server
  linux/arm64 29.7.2 under emulation).
- Manifest: checked-in `spec/reference/corpus-v0.json`
  (`141c7f05…6181`); development mode refuses indices 96–127 before
  renderer or artifact-store access.
- Stores: first run unchanged at `/Users/joseph/dev/gf180-issue19-corpus-store`;
  repeat store a new physical root that did not exist before the run
  (`/Users/joseph/dev/gf180-issue20-repeat-store`). A single-case smoke render
  used a separate scratch store and is not part of the repeat corpus.
- Repeat run ID: `1c3ca47b7e184eeca707b38dd89ca8ae`
  (first run: `9c443eed363e4874a07328d8ddadd095`).

## Accounting

- Counts: expected 96, observed 96, success 96, failure 0, attempt 96,
  retry 0, resume 0.
- Render elapsed time 1559.3 s (per-attempt min/median/max
  13.6/15.5/25.9 s); store 390 files, 71,818,190 bytes.
- Exact-byte verification without TorchSynth (`python3 -S … --verify`)
  passed against the recorded run-envelope digest
  `c61a8678…5739cc7`.

## Byte-repeat result

`tools/compare_development_corpus.py` strictly audits each side against its
receipt with the landed audit machinery, refuses aliased/reused stores, then
compares complete original bytes per identity and artifact class:

- Corpus index documents: byte-identical. Both stores carry index
  `1b81701327150597fd6fd70b24d642bc87519a0c76777bc695506d3ae8bf80cd`
  (the v1 index contains no run-local fields, so a true repeat is
  byte-identical, not merely equivalent).
- Per case (96/96): index record EQUAL, metadata bytes EQUAL (96 unique
  immutable artifacts with identical `artifact_id`s), audio bytes EQUAL
  (each 176,400 finite little-endian float32 samples, 705,600 bytes).
  No first-divergent byte exists and no numeric error rows were needed.
- Traces: audio-only profile on both sides; absent traces are recorded
  NOT REQUESTED, not fabricated equality of captured signals.
- Run envelopes: immutable content EQUAL (schema, status, counts, attempt
  sequence/case/kind/status/artifact, and receipt identity fields including
  request/worker/source hashes, runtime, rng sentinel, image, exit code,
  warning categories, observations). Volatile telemetry differed as expected
  and is recorded, never compared as audio equality: run IDs, elapsed times,
  execution UUIDs, attempt start timestamps, and run-local command vectors.
  Plan documents differ only in the volatile `run_id`.

Verdict: COMPLETE-REPEAT (comparator exit 0). The comparison report is
deterministic: repeated runs over unchanged stores produce byte-identical
reports; its digest is recorded in the receipt.

## Evidence receipt and raw-byte custody

`sim/reference/development-corpus-repeat.json` is the bounded evidence
receipt: the exact commands and exit statuses (the render wrapper lost its
own exit status; success is established by the captured run JSON and the
independent verify/audit/comparator exit codes recorded alongside), the
embedded second-run plan and index documents, run/index/plan/envelope SHA-256
digests, producer/runtime identity, storage and timing facts, the bounded
comparison summary, and honest limits. The ~69 MiB repeat store is
operator-retained at the recorded path and is not committed; every byte is
authenticated by the per-artifact metadata hashes inside the byte-identical
index and can be re-verified with the audit's store mode.

## Audit and reproduction

```sh
# receipt-only (stdlib; internal consistency and digests)
python3 tools/audit_development_corpus.py
python3 tools/audit_development_corpus.py --receipt sim/reference/development-corpus-repeat.json
# with the retained raw stores: landed read-only verifier plus audio re-scan
python3 tools/audit_development_corpus.py --store /physical/path/corpus-store
python3 tools/audit_development_corpus.py --receipt sim/reference/development-corpus-repeat.json --store /physical/path/repeat-store
# independent byte comparison of both stores (read-only)
python3 tools/compare_development_corpus.py \
  --first-receipt sim/reference/development-corpus-first.json \
  --first-store /physical/path/corpus-store \
  --second-receipt sim/reference/development-corpus-repeat.json \
  --second-store /physical/path/repeat-store
```

The comparator refuses missing store roots without creating them, refuses
same-root/symlinked stores, and inherits the storage layer's refusal of
aliased (hardlinked) files; negative controls for all of these, for signed
zero, single-bit audio mutation, tampered receipts and falsified counters,
denominator violations, holdout identities, and the no-write fence are in
`tests/test_corpus_repeat.py` with synthetic data only.

## Limits

Single repeat execution on one admitted host; not multi-host, sound-fidelity,
scalar-qualification, or hardware evidence. Byte-repeat establishes bounded
corpus repeatability only — it is not independent float conformance (#43),
the listening/corruption protocol (#44), or population drift diagnostics
(#46), and it does not populate hardware PASS rows (#87). Holdout
characterization remains #21; traces were not requested.
