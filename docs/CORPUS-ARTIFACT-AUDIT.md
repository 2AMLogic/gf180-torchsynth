# Corpus artifact integrated evidence audit (issue #4)

This audit maps the original acceptance criteria and Evidence requirement of
issue #4 to committed records and reproducible checks. It changes no schema,
store, runner, source profile, arithmetic/noise policy, or holdout boundary.
The normative source remains TorchSynth
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`; the product profile remains the
four-second, 44.1 kHz, default-nebula clip.

Verdict vocabulary: **established** (demonstrated by a named record or check at
the stated scope), **gap** (a requirement is not demonstrated), **not run**
(a check that could apply was not executed here). An inspected historical
record is never counted as a check executed by this audit.

**Overall result: the original criteria are established at synthetic-test
scope. Real-render evidence exists but is historical only (the two-case #15
smoke record, the 96-case #19 development run, and its #20 byte-repeat, all
inspected rather than re-executed here), and four bounded gaps remain (G1-G4
below; G3 narrowed on 2026-10-10 to retained historical records). Issue #4 must stay open; this audit does not close it.**

**Re-verification (2026-10-07, revision `1386c4d314c6308dcb5ac4e1db580b31cfa4ef0b`):
verdicts unchanged. All 68 focused tests and the contract check passed again.
G1 is narrowed: the producer commit is retrievable from GitHub, but no
reproduction ran. G2 is unchanged. G3 now has a concrete source location and
follow-up #289. G4 is unchanged.**

**Re-verification (2026-10-07, revision `6d1bdffa678958bc80228e0dc0bebb913feba9fa`):
verdicts unchanged. Focused suites and the contract check passed again (68
tests, 0 skips). Changes since `1386c4d` under `src`, `tools`, `tests`,
`sim/reference`, and `spec` are the unrelated directed-trace-paths addition
(#286 lineage) and trace-spec wording; none touches the artifact, storage,
runner, fixtures, smoke record, or the three audited specs. G1, G2, and G4 are
unchanged (producer `224eb15a...` still absent from the local object store,
`git cat-file -t` exit 128; no `corpus-smoke-15` or run directory found on this
worker; #289 for G3 is still open). Details under "Re-verification run at
`6d1bdff`".**

**Re-verification (2026-10-07, revision `a4d7a3f0b9f9c76a6e2dd7959bc2c474ec399b84`):
verdicts unchanged. Focused suites and the contract check passed again (68
tests, 0 skips). `git diff --stat 6d1bdff HEAD -- src tools tests sim/reference spec`
shows only documentation additions to `spec/ONESHOT-E2E.md`,
`spec/TRACE-ARTIFACTS.md`, `spec/TRACE-CAPTURE.md`, and `spec/TRACE-REGISTRY.md`;
nothing under the artifact, storage, runner, fixtures, smoke record, or the three
audited specs changed. G1 (producer `224eb15a...` still absent locally,
`git cat-file -t` exit 128), G2 (no `corpus-smoke-15` directory found with
`find / -xdev`), and G4 are unchanged; #289 for G3 is still open. Executed on
Linux 6.17.0-1019-aws, Python 3.12.3, serially, in the clean worktree
`.loom/worktrees/issue-4`: `test_artifacts.py` 28 OK (0.364 s),
`test_storage.py` 16 OK (1.618 s), `test_artifact_renderer.py` 8 OK (5.516 s),
`test_corpus.py` 16 OK (42.396 s), `tools/check_contract.py` exit 0. No render,
store verification, or reproduction was run (class C remains none).**

**Re-verification (2026-10-10, revision `74a74e740fa4abb63586358679b335eb97685a61`):
audited inputs changed since `a4d7a3f` (#289 receipt portability, PR #308, commit
`ed8c5fc`; renderer, corpus, runner spec, run schema description, and tests).
**G3 is narrowed: for newly produced runs it is now established at synthetic-test
scope** (portable-receipt policy, validator, and synthetic rejection tests, all
passing here). The retained historical records still contain host paths and stay
unmodified, so the historical part of G3 remains a recorded, deliberately
deferred limit. G1 (`git cat-file -t` exit 128), G2 (no `corpus-smoke-15` store
or run directory found), and G4 are unchanged. Focused suites passed: 83 tests,
0 skips (28 + 16 + 17 + 22), `tools/check_contract.py` exit 0. No render, store
verification, reproduction, or holdout access was run (class C remains none).
Details under "Re-verification run at `74a74e7`".**

**Re-verification (2026-10-10, revision `ad0f524527848952d65ba81a527859a2a3c797aa`):
verdicts unchanged. `git diff --stat 74a74e7 HEAD -- src tools tests sim/reference spec`
is empty (only Loom resync and docs commits landed), so nothing audited changed.
Executed serially on Linux 7.0.0-1014-aws, Python 3.12.3, in the clean worktree
`.loom/worktrees/issue-4`: `test_artifacts.py` 28 OK (0.354 s), `test_storage.py`
16 OK (1.647 s), `test_artifact_renderer.py` 17 OK (7.702 s), `test_corpus.py` 22 OK
(61.424 s), 0 skips (83 tests), `tools/check_contract.py` exit 0. Observation for
G1: `git cat-file -t 224eb15a599171b3a8b65ca72457f7e452b83c8e` now prints `commit`
(exit 0) in this checkout's object store, which differs from the exit 128 recorded
earlier; the object is locally present (most likely from an earlier PR-ref fetch),
but this audit did not check it out, and no reproduction ran. G1 remains a gap
because the reproduction itself is the missing measurement. G2 (no `corpus-smoke-15`
path found by `find / -xdev`), G3 (historical part only; #289 reads `closed`), and
G4 are unchanged. No render, store verification, reproduction, or holdout access
was run (class C remains none).**

**Re-verification (2026-10-10, revision `5f977ca18529d4ee06cda850c54c2829a8a718a4`):
verdicts unchanged. `git diff --stat ad0f524 HEAD -- src tools tests sim/reference spec`
is empty, so nothing audited changed. Executed serially on Linux 7.0.0-1014-aws,
Python 3.12.3, in the clean worktree `.loom/worktrees/issue-4`: `test_artifacts.py`
28 OK (0.397 s), `test_storage.py` 16 OK (4.475 s), `test_artifact_renderer.py` 17 OK
(14.609 s), `test_corpus.py` 22 OK (70.478 s), 0 failures (83 tests),
`tools/check_contract.py` exit 0. Observation for G1: in this checkout's object store
`git cat-file -t 224eb15a599171b3a8b65ca72457f7e452b83c8e` now exits 128 (object
absent), which differs from the exit 0 recorded at `ad0f524`; the local presence of the
producer commit is therefore not stable across checkouts, and no reproduction ran. G2
(`find / -xdev -name 'corpus-smoke-15*'` found nothing), the historical part of G3, and
G4 are unchanged. No render, store verification, reproduction, or holdout access was
run (class C remains none).**

## Evidence classes

| Class | Meaning in this audit |
| --- | --- |
| A. Fresh synthetic tests | Executed by this audit at revision `1386c4d314c6308dcb5ac4e1db580b31cfa4ef0b` (2026-10-07 re-verification), most recently at `ad0f524527848952d65ba81a527859a2a3c797aa` (2026-10-10; previously `74a74e740fa4abb63586358679b335eb97685a61`), and earlier at `e29a8a946f564cbea4b0c318f0a86af1e3baa0e0` (PR #285) (see Executed checks) |
| B. Historical publication inspection | `sim/reference/corpus-smoke.json` (#15), `sim/reference/development-corpus-first.json` (#19), and `sim/reference/development-corpus-repeat.json` (#20) read and cross-checked, not re-executed |
| C. Fresh runtime measurement | None. No Docker/amd64 render, store verification, or reproduction was run in either audit pass |

## Executed checks (class A)

### Re-verification run at `74a74e7` (2026-10-10)

Environment: Linux 7.0.0-1014-aws (shared AWS dispatch worker), Python 3.12.3,
`TORCHSYNTH_ROOT` unset, no Torch/Docker used. Clean worktree
`.loom/worktrees/issue-4` at `HEAD` `74a74e740fa4abb63586358679b335eb97685a61`
(equal to `origin/main`). Run serially. Wall time is from `time`; the suite
time is the unittest-reported value.

| Command | Exit | Result | Suite time | Wall time |
| --- | --- | --- | --- | --- |
| `python3 -m unittest discover -s tests -p test_artifacts.py -v` | 0 | 28 tests OK, 0 skips | 0.460 s | 0.55 s |
| `python3 -m unittest discover -s tests -p test_storage.py -v` | 0 | 16 tests OK, 0 skips | 2.638 s | 2.75 s |
| `python3 -m unittest discover -s tests -p test_artifact_renderer.py -v` | 0 | 17 tests OK, 0 skips | 11.248 s | 11.46 s |
| `python3 -m unittest discover -s tests -p test_corpus.py -v` | 0 | 22 tests OK, 0 skips | 93.925 s | 94.12 s |
| `python3 tools/check_contract.py` | 0 | `contract manifests are internally consistent` | n/a | n/a |

Changes since `a4d7a3f` (`git diff --stat a4d7a3f..HEAD` over
`spec/ARTIFACT-CONTRACT.md`, `spec/CORPUS-RUNNER.md`,
`spec/schemas/corpus-run-v1.schema.json`, `src/torchsynth_voice/artifact_renderer.py`,
`src/torchsynth_voice/corpus.py`, `tests/test_artifact_renderer.py`,
`tests/test_corpus.py`, `tools/render_corpus.py`): 8 files, 1167 insertions, 57
deletions. `test_artifact_renderer.py` grew from 8 to 17 tests and
`test_corpus.py` from 16 to 22. The storage, artifact-contract tests, fixtures,
and `sim/reference/corpus-smoke.json` were not part of that diff set.

G3 reassessment (code and tests inspected and executed, not rendered):

- Policy: `spec/CORPUS-RUNNER.md` "Portable attempt receipts" (and the
  "Historical v1 records" paragraph); `spec/ARTIFACT-CONTRACT.md` now points
  corpus run receipts at it.
- Implementation: `src/torchsynth_voice/artifact_renderer.py`
  `ReceiptPortabilityError` (line 79), `host_path_findings` (112),
  `require_portable_receipt` (132), `failure_receipt` (246),
  `validate_failure_receipt` (258). `src/torchsynth_voice/corpus.py` calls
  `require_portable_receipt` before the immutable finish record is written
  (`run_corpus`, around lines 655-690), and `validate_attempt_receipt` (371) runs
  from `validate_run` (385; default `receipt_policy="portable-v1"`, call at line 457).
- Schema: `spec/schemas/corpus-run-v1.schema.json` still constrains `receipt`
  structurally as an object; only its description changed. The semantic rule is
  enforced by `validate_run`, not by the JSON schema alone.
- Synthetic rejection tests (all passed here): `test_artifact_renderer.py`
  `test_every_host_path_form_rejected`, `test_diagnostics_never_echo_the_offending_text`,
  `test_declared_container_paths_and_portable_values_accepted`,
  `test_command_requires_marker_and_placeholder_mount_sources`,
  `test_failure_diagnostic_withholds_path_bearing_messages`,
  `test_render_artifact_refuses_path_bearing_receipt_before_publication`,
  `test_audio_only_backend_publishes_placeholder_command`,
  `test_worker_failure_diagnostic_is_withheld_when_path_bearing`; `test_corpus.py`
  `test_path_bearing_exception_never_reaches_a_finish_record`,
  `test_non_portable_success_receipt_recorded_as_failed_then_retried`,
  `test_validator_rejects_path_bearing_receipts_in_any_attempt`,
  `test_validator_requires_portable_failure_diagnostic`,
  `test_portable_command_and_container_paths_accepted`.
- Historical records: `test_retained_historical_receipts_classified_not_portable_read_only`
  (renderer tests) shows the retained smoke render receipts are still classified
  non-portable (exactly two `posix-absolute` findings per receipt, the host mount
  sources) and that the file bytes are unchanged.
  `test_historical_path_bearing_run_explicit_policy_read_only` (corpus tests)
  shows the explicit `historical-v1` verify mode (`tools/render_corpus.py --verify
  ... --historical-receipts`) runs read-only and never reports such receipts as
  portable. The retained evidence JSONs were not modified.
- Issue #289 reads `closed` via the GitHub REST API (2026-10-10).
- The scan for G3 locations in the historical JSON records is unchanged from the
  earlier passes; it was not repeated.

AC6 (holdout boundary) tests relevant here: the corpus tests named under AC6
all passed (`test_default_has_only_development`, `test_mixed_holdout_refused`,
`test_mixed_range_refused_before_any_renderer_or_store_access`,
`test_holdout_admission_one_shot_and_same_run_resume_synthetic_only`,
`test_interrupted_holdout_reuse_retains_one_shot_admission`). Since `a4d7a3f`
the repository also gained `tests/test_holdout_seal.py` (43 tests, from commit
`e13c33b`, Part of #55, extended by `3513df8`) covering the seal manifest,
tamper refusal, and refusal gates. That file is outside this audit's focused
set and was **not run** here, so it is cited as an existing test file only. No
holdout identity was rendered or accessed.

Producer reachability (G1): `git cat-file -t 224eb15a599171b3a8b65ca72457f7e452b83c8e`
in the worktree failed with exit 128 (`could not get object info`). The pull
request ref fetch from the earlier pass was not repeated.
Raw store (G2): `find / -xdev` for names `corpus-smoke*` and `corpus-run*`
returned only copies of `sim/reference/corpus-smoke.json` and
`spec/schemas/corpus-run-v1.schema.json` inside repository worktrees; no
`corpus-smoke-15` store or `runs/6e198bc9...` directory exists. Store
verification: **not run**.

Not run: full suite, `tests/test_holdout_seal.py`, `python3 -S` standalone suite
runs, concurrent-writer loop, ruff, compileall, any store verification or render.

### Re-verification run at `6d1bdff` (2026-10-07)

Environment: Linux 6.17.0-1019-aws (shared AWS dispatch worker), Python 3.12.3,
`TORCHSYNTH_ROOT` unset, no Torch/Docker used. Clean worktree
`.loom/worktrees/issue-4` at `HEAD` `6d1bdffa678958bc80228e0dc0bebb913feba9fa`
(equal to `origin/main`). Run serially.

| Command | Exit | Result | Wall time |
| --- | --- | --- | --- |
| `python3 -m unittest discover -s tests -p test_artifacts.py -v` | 0 | 28 tests OK, 0 skips | 0.455 s |
| `python3 -m unittest discover -s tests -p test_storage.py -v` | 0 | 16 tests OK, 0 skips | 1.599 s |
| `python3 -m unittest discover -s tests -p test_artifact_renderer.py -v` | 0 | 8 tests OK, 0 skips | 3.961 s |
| `python3 -m unittest discover -s tests -p test_corpus.py -v` | 0 | 16 tests OK, 0 skips | 35.343 s |
| `python3 tools/check_contract.py` | 0 | `contract manifests are internally consistent` | n/a |

Not run: full suite, `python3 -S` standalone suite runs, concurrent-writer
loop, ruff, compileall, any store verification or render (raw bytes absent).
The historical smoke and development records were not re-read for changes
because `git diff --name-status 1386c4d HEAD -- sim/reference` shows only the
unrelated `directed-trace-paths-v1.json` addition.

### Re-verification run at `1386c4d` (2026-10-07)

Environment: Linux 7.0.0-1010-aws (shared AWS dispatch worker), Python 3.12.3,
`TORCHSYNTH_ROOT` unset, no Torch/Docker needed or used. Run from a clean
isolated worktree (`.loom/worktrees/issue-4`) at `HEAD`
`1386c4d314c6308dcb5ac4e1db580b31cfa4ef0b`, which equalled `origin/main` after
`git fetch`. Before any edit, `git status --short` was empty. Sequential, no
parallelism. `git diff --stat e29a8a9 1386c4d` over `src`, `tools`, `tests`,
`tests/fixtures/artifacts`, `sim/reference`, and the three artifact/storage/runner
specs shows only the PR #285 addition to `spec/CORPUS-RUNNER.md`. No code, test,
fixture, or evidence input changed between the two audit passes.

| Command | Exit | Result | Wall time |
| --- | --- | --- | --- |
| `python3 -m unittest discover -s tests -p test_artifacts.py -v` | 0 | 28 tests OK, 0 skips | 0.41 s |
| `python3 -m unittest discover -s tests -p test_storage.py -v` | 0 | 16 tests OK, 0 skips | 1.67 s |
| `python3 -m unittest discover -s tests -p test_artifact_renderer.py -v` | 0 | 8 tests OK, 0 skips | 3.84 s |
| `python3 -m unittest discover -s tests -p test_corpus.py -v` | 0 | 16 tests OK, 0 skips | 32.25 s |
| `python3 tools/check_contract.py` | 0 | `contract manifests are internally consistent` | n/a |

All 16 `test_corpus.py` tests named in this audit (for example
`test_two_case_resume_zero_recomputation_and_stable_index`,
`test_interrupted_index_publication_resumes_without_rendering`,
`test_verification_without_site_packages_is_read_only`,
`test_holdout_admission_one_shot_and_same_run_resume_synthetic_only`) reported
`ok`. The same exclusions apply as in the earlier run: no full suite, no
standalone `python3 -S` suite runs, no concurrent-writer loop, ruff, or compileall.

Additional read-only checks in this pass:

- Clean-tree provenance consistency: both smoke artifacts record
  `project_git.untracked_sha256 = 51d865c4...d927`. This equals
  `sha256(canonical_bytes({}))` computed with `src/torchsynth_voice/artifacts.py`
  (`python3 -I`, `sys.path` set to `src`). `diff_sha256 = e3b0c442...b855` is
  `sha256(b"")`. This is the clean state that `spec/ARTIFACT-CONTRACT.md` defines
  (empty diff bytes and the empty map), and it is consistent with `dirty=false`.
- Producer reachability (G1): see the updated G1 row.
- Raw store (G2): `find / -xdev -type d -name corpus-smoke-15` and a search for
  a `runs/6e198bc9a03f4a929ac0a59a93c775cf` directory found nothing on this
  worker. The primary checkout has no `out/` directory.

### Earlier run at `e29a8a9` (PR #285)

Environment: Linux 6.17.0-1019-aws (AWS dispatch worker), Python 3.12.3,
`TORCHSYNTH_ROOT` unset, no Torch/Docker needed or used. Run from a clean
isolated worktree (`.loom/worktrees/issue-4`) at `HEAD`
`e29a8a946f564cbea4b0c318f0a86af1e3baa0e0`, which equalled `origin/main` after
`git fetch`. Sequential, no parallelism. The only working-tree change at the
time was the documentation addition to `spec/CORPUS-RUNNER.md` described under
criterion E1; no test input changed.

| Command | Exit | Result | Wall time |
| --- | --- | --- | --- |
| `python3 -m unittest discover -s tests -p test_artifacts.py -v` | 0 | 28 tests OK, 0 skips | 0.377 s |
| `python3 -m unittest discover -s tests -p test_storage.py -v` | 0 | 16 tests OK, 0 skips | 1.574 s |
| `python3 -m unittest discover -s tests -p test_artifact_renderer.py -v` | 0 | 8 tests OK, 0 skips | 5.654 s |
| `python3 -m unittest discover -s tests -p test_corpus.py -v` | 0 | 16 tests OK, 0 skips | 43.964 s |
| `python3 tools/check_contract.py` | 0 | `contract manifests are internally consistent` | n/a |

Total 68 focused tests passed. Not run: the full test suite, `python3 -S`
storage/corpus suites, the repeated concurrent-writer loop, ruff, compileall.
The `-S` and concurrency results quoted in `spec/ARTIFACT-STORAGE.md` are
historical (class B-like documentation, macOS, Python 3.14.7), not rerun here.
`test_corpus.py` and `test_storage.py` do exercise their own `-S`/no-site-package
behavior internally through subprocesses (tests
`test_verification_without_site_packages_is_read_only` and
`test_storage_operates_without_site_packages_or_renderer_imports`), and those passed.
The synthetic renderer tests use an injected backend; none touched Docker.

Additional read-only consistency script (inline, uncommitted, run with
`python3 -I` and `sys.path` set to `src`), over the **embedded** documents in
`sim/reference/corpus-smoke.json` only:

- `content_id(artifacts[g].inputs.value) == artifacts[g].artifact_id` for
  `global-0` and `global-1`: true for both.
- `validate_artifact(artifacts[g])` passed for both (complete status, 78
  normalized and 78 physical names).
- `plan.template` equals each artifact's `project_git`, `renderer_version`,
  `runtime`, `source`, `profile`, `execution`, `requested_traces`,
  `trace_registry_version`, `locks_physical`: true for all nine fields, both
  artifacts.
- `index.cases[i].artifact.artifact_id` and `.fixture` match the embedded
  artifacts; `plan.manifest.sha256 == index.corpus_manifest_sha256`
  (`141c7f05...6181`); `plan.template.runtime.lock_sha256 ==
  index.runtime_lock_sha256` (`1966379f...1b25`).
- Recomputing `sha256(canonical_bytes(doc))` (with and without trailing newline)
  for the embedded artifact, index, plan, and run documents does **not**
  reproduce the recorded exact-byte digests (for example index
  `964dbda2...308b`, artifact `2efbdcea...4d40`). This is expected because the
  stored files are exact serialized bytes that this publication does not
  retain, and it is **not** a verification failure or success. It confirms that
  exact-byte digests cannot be re-derived from embedded metadata, and none is
  claimed to be.
- A scan of embedded `artifacts`, `plan`, and `index` for `/Users`, `/home`,
  `/private` found none.
- `validate_corpus_index(index)` returned without raising (it returns `None`).
  The script did not pass the loaded artifacts, so cross-artifact checks come
  from the field comparisons above.

## Historical smoke publication inspection (class B)

Record: `sim/reference/corpus-smoke.json` (`status: PASS`, schema
`torchsynth-corpus-smoke-evidence` v1), last changed by commit `25495a3`. Producer
commit `224eb15a599171b3a8b65ca72457f7e452b83c8e` (clean:
`project_git.dirty=false`, `diff_sha256=e3b0c442...b855`, the empty-input
digest). Runtime profile `release-mkl-compatible-v1`, renderer
`artifact-renderer-v1-release-mkl-compatible-v1`, Python 3.9.13, Torch
1.12.1+cpu, Linux x86_64 under emulation.

| Item | Recorded key and value |
| --- | --- |
| Selected cases | `selected_development_indices = [0, 1]`; `plan.selection` is two `development` entries; `full_96_case_run=false`; `actual_holdout_access=false` |
| Artifacts | `global-0` `ra1-ee508c86...7d6c` (metadata sha256 `2efbdcea...4d40`, audio sha256 `acb33316...0463`, 705,600 B, 176,400 samples); `global-1` `ra1-615a98a7...3371` (metadata `3875dc4b...4c4e`, audio `6fc07a9f...b095`, 705,600 B, 176,400 samples) |
| Input identity | Both: `canonical-batch`, batch 32, seed 13, noise slots 0/1, upstream `synth1B1-0-0`/`-1`, source commit `2b0964d4...`, trace registry `audio-only-v1`, empty `requested_traces` and `traces.value` |
| Manifest/lock | manifest sha256 `141c7f0550c0...6181` (630 B); runtime lock `1966379fb95f...1b25` |
| Plan | run `6e198bc9a03f4a929ac0a59a93c775cf`, `runs/<run>/plan.json` sha256 `224864ded8d788b102c540417b3c217766f9426ec04f7eda4a4b22c1046c417c` (2,861 B) |
| Index | `indexes/964dbda22092cb92aff13a6a2054b811af5e749e583174e524fa71e9e1a5308b.json` (1,809 B), corpus id `ci1-1d697b03...2d88`, expected 2 / observed 2, no failures; identical `ref`/`sha256` in `render_run.index` and `resumed_run.index` (`exact_index_reused=true`) |
| First run counts | attempt 2, expected 2, failure 0, observed 2, resume 0, retry 0, success 2; 2 `render` events; elapsed 36.908 s |
| Resume counts | attempt 2, resume 2, retry 0, success 2; events are the two prior `render` plus two `resume` events (0.152 s for `global-0`, empty receipt); elapsed 37.208 s; `additional_render_attempts_on_resume=0` |
| Envelopes | first `render_envelope_sha256 = 2203c30fb2383746a1d4b93562ba1c2c05268ec37ba7562076a301ae51409bbb`; resumed `resume_envelope_sha256 = 122cd1cf657b61ca147386b0392d54a41ae88ee4a62539c9cddc1210092d7818` |
| Command receipts | three commands, `command_exit_codes=[0,0,0]`: render `--indices 0 1`; `--resume 6e198bc9...` ; `python3 -S ... --verify 6e198bc9... --run-sha256 122cd1cf...7818` (matches `resume_envelope_sha256`) |
| Producer-side checks | `local_checks`: 288 tests, 0 skips, exit 0 (160.483 s, with a private exact upstream archive); `check_contract` exit 0; compileall exit 0; ruff exit 0; schema check exit 0. Recorded only, not rerun here |
| Raw comparison | `global_0_qualified_raw_hash_comparisons`: audio, noise, normalized, physical, pre_normalization all `true` (recorded, not rechecked: payloads absent) |

Explicit publication limits (verbatim `limits`): two development cases only;
audio-only traces; independent repeat must use the clean producer commit;
raw audio remains in the ignored local store and the embedded documents
authenticate metadata and recorded hashes, not absent raw bytes; the sibling
`<operator-home>/dev/torchsynth` checkout at `304824db...` was refused; bounded
software rendering and storage only. (Host paths are replaced by placeholders
in this audit; the raw string is in `corpus-smoke.json` at `limits[4]`.)

Observed in the record: `raw_audio_committed=false`,
`raw_store_relative="out/corpus-smoke-15"` (git-ignored by `.gitignore`
`out/`). Real production payloads are therefore unavailable.

## Historical development corpus inspection (class B)

These records were read and their keys cross-checked; no command they record
was re-executed by this audit, and their raw stores are operator-retained,
not available here.

| Item | Recorded key and value |
| --- | --- |
| First run (#19) | `sim/reference/development-corpus-first.json`, `status: PASS`, schema `torchsynth-development-corpus-evidence` v1, added by commit `b367032`; narrative in `spec/DEVELOPMENT-CORPUS.md`. Producer `producer_git.commit = bd8b35e4...f798` (`dirty=false`), runtime profile `release-mkl-compatible-v1`, `manifest_sha256 = 141c7f05...6181` (same manifest as the smoke record) |
| First-run counts | `counts`: attempt 96, expected 96, failure 0, observed 96, resume 0, retry 0, success 96; `expectation.development_indices` = 0-95; `expectation.holdout_indices_rendered = []`; `plan.admission = null` |
| First-run identity | `run.run_id = 9c443eed363e4874a07328d8ddadd095`, `run.envelope_sha256 = 82406940...93e0df`, `run.index_sha256 = 1b817013...80cd` |
| First-run command receipts | `commands[]`: three commands, exit codes 0, 0, 0 (single-case scratch smoke `--indices 0`; default 96-case render; `python3 -S ... --verify 9c443eed... --run-sha256 82406940...93e0df`). `verification[0]`: `status: complete`, exit 0, counts 96/96 success, failure 0 |
| First-run store | `storage`: `artifact_directories` 96, `files` 390, `bytes` 71,820,533, `audio_bytes_each` 705,600, `committed_to_repository=false` |
| Repeat (#20) | `sim/reference/development-corpus-repeat.json`, `status: complete`, `evidence_kind: independent-byte-repeat`, added by commit `acc3746`; narrative in `spec/DEVELOPMENT-CORPUS-REPEAT.md`. Same producer commit `bd8b35e4...`, fresh store; run `1c3ca47b...a8ae`, counts 96/96 success, failure 0 |
| Repeat comparison | `comparison.verdict = COMPLETE-REPEAT`; `index_bytes.verdict = EQUAL` (both `1b817013...80cd`); `per_class_counts`: audio_equal 96, metadata_equal 96, index_record_equal 96, mismatches 0, missing 0, refused 0 |
| Repeat command receipts | `commands[]`: five commands; verify, audit, and compare exit 0; the 96-case render command has `exit_code: null` with `exit_code_note` stating the launcher wrapper lost the status and success rests on the captured run JSON and the later verify/audit/compare commands |

Limits (from each record's `limits`): single execution on one admitted Apple
M5 / Docker-emulated host; raw bytes not embedded; no holdout identity rendered,
admitted, or inspected; no tolerance, quality, or sound-fidelity conclusion.
Traces were not requested (audio-only).

## Original acceptance criteria

| # | Original criterion | Verdict | Evidence and limits |
| --- | --- | --- | --- |
| AC1 | A two-case test corpus renders, validates, resumes without recomputation, and produces a stable index | **established** (synthetic, fresh) and corroborated by historical record | A: `tests/test_corpus.py::test_two_case_resume_zero_recomputation_and_stable_index` passed (backend call count unchanged on resume, byte-identical index). B: smoke record shows 2 cases, resume attempt count 2 unchanged, 0 additional render attempts, same index sha256 `964dbda2...308b` in both runs. B (real scale): the #19 record shows 96/96 success with `resume` 0 and a `python3 -S --verify` exit 0, and #20 reproduced a byte-identical index. Limit: the real render and real exact-byte verification were not re-executed or re-verified (see G1, G2); recorded PASS is not a test run here |
| AC2 | Interrupted/staging artifacts cannot be mistaken for complete artifacts | **established** (synthetic, fresh) | A: storage `test_process_exit_during_copy_leaves_only_incomplete_private_stage`, `test_process_exit_before_rename_is_not_discoverable_and_retry_succeeds`, `test_failure_after_atomic_rename_can_resume_complete_bytes`, `test_exception_cleanup_only_removes_own_staging`; corpus `test_interrupted_attempt_retained_on_resume`, `test_interrupted_index_publication_resumes_without_rendering`, `test_interrupted_reuse_retains_completed_artifact_without_rendering`. Limit: process interruption only; power-loss durability and network filesystems unqualified (`spec/ARTIFACT-STORAGE.md`) |
| AC3 | A changed source hash, runtime identity, lock, sample count, or audio hash produces a hard failure | **established** (synthetic, fresh) | A: renderer `test_request_mismatch_before_backend_access` (source commit, torch version, duration, batch size refuse before backend access) and `test_worker_source_gate_precedes_numerical_import`; artifacts `test_stale_content_id_is_rejected` (changed `runtime.lock_sha256`), `test_cross_artifact_checks` (index lock/fixture mismatch), `test_every_input_leaf_affects_identity`; renderer `test_invalid_observations_never_publish` (short audio, wrong noise hash); storage `test_missing_extra_truncated_corrupt_files_and_empty_directories`; corpus `test_corrupt_extra_missing_metadata_and_payload_refused` and `test_stale_resume_refused_before_renderer` (changed `project_git.commit`). Limit: coverage is by representative mutations and the leaf-identity test; no test mutates every source file hash individually against the real worker |
| AC4 | Reordered parameter arrays do not change name-keyed patch meaning | **established** (synthetic, fresh) | A: artifacts `test_key_order_is_irrelevant` (reversed `physical_by_name` and top-level key order give identical `content_id`), `test_diagnostic_orders_are_permutations_only` (orders are diagnostic only), `test_rejects_positional_missing_extra_and_mismatched_parameters`, `test_all_canonical_parameter_names_maps_locks_and_orders`. B: both smoke artifacts carry 78 name-keyed normalized and physical values. Limit: no real TorchSynth reordering was exercised |
| AC5 | Duplicate identities with conflicting bytes are rejected | **established** (synthetic, fresh) | A: storage `test_collision_reports_both_metadata_and_changed_output_hashes`, `test_metadata_whitespace_only_change_is_a_collision`, `test_two_process_writers_converge_or_report_collision`; renderer `test_same_id_different_bytes_collision_preserves_original`; artifacts `test_duplicate_case_or_sound_or_artifact_is_rejected`. Limit: cooperating local POSIX writers only |
| AC6 | Holdout artifacts have an access boundary documented and tested | **established** (synthetic, fresh; documentation inspected) | Documented in `spec/CORPUS-RUNNER.md` "One-shot holdout gate". A: corpus `test_default_has_only_development`, `test_mixed_holdout_refused`, `test_mixed_range_refused_before_any_renderer_or_store_access`, `test_holdout_admission_one_shot_and_same_run_resume_synthetic_only`, `test_interrupted_holdout_reuse_retains_one_shot_admission`. B: `actual_holdout_access=false`, `plan.admission=null`. Limit: synthetic payloads only; operator-attested freeze and operator-held ledger, no remote admission service; no real holdout rendered or accessed, and none was authorized |
| AC7 | Unit tests cover resume, collision, partial-write, corrupt-hash, missing-sample, and dirty-tree metadata | **established** (fresh) | Resume: AC1 tests. Collision: AC5. Partial write: AC2. Corrupt hash: storage corrupt/truncated test, corpus corrupt payload test, `test_corrupt_existing_artifact_never_resumes_or_gets_replaced`. Missing sample: storage missing/truncated files, renderer short audio, schema sample count of 176,400. Dirty tree: `test_artifacts.py::test_clean_and_dirty_git_digests`, `test_missing_provenance_never_defaults`. Limit: "missing-sample" is covered as missing/short audio payload and sample-count validation, not as a distinct sample-gap test |
| AC8 | The public metadata schema contains no absolute user path | **established** for artifact, index, and plan documents and (synthetic, fresh, since #289) for newly produced run receipts; **see G3** for retained historical receipts | A: `tests/fixtures/artifacts/rejections.json` unsafe-reference mutations (POSIX/Windows/UNC/parent-traversal) all rejected by `test_negative_fixture_mutations`; `test_path_traversal_aliases_and_reserved_metadata_are_rejected`. A (script): no `/Users`, `/home`, `/private` in embedded smoke `artifacts`, `plan`, `index`. The same scan over the embedded `plan` and `index` of both `development-corpus-*.json` records found none. A (since #289): the portable-receipt tests listed under the `74a74e7` run. Limit: the strings in `rejections.json` are intentional negative inputs. The retained run `attempts[].receipt.command` strings and `local_checks`/`limits` text do contain host paths (G3) |

## Evidence requirement (original "Evidence" section)

| Requirement | Verdict | Evidence and limits |
| --- | --- | --- |
| Commit a tiny synthetic fixture/index suitable for CI | **established** | `tests/fixtures/artifacts/complete.json` (8,587 B), `index.json` (1,044 B), `rejections.json` (6,406 B), consumed by `tests/test_artifacts.py`, which passed here. Synthetic only; no sound-fidelity meaning |
| Keep full float audio out of Git unless explicitly decided | **established** | No audio files are committed; smoke record has `raw_audio_committed=false`; the smoke store path is under git-ignored `out/`. Storage tests generate zero-filled audio in temporary directories |
| Document exact commands for the 128-case corpus | **established after this change**; development command historically executed, holdout command **never run** | Before this change `spec/CORPUS-RUNNER.md` gave only 0/1 development commands and a 96-case default, with no holdout invocation. This PR adds the "Full 128-case corpus commands and storage" section (96 development run, separate 32-case one-shot holdout run with `--holdout-once --frozen-rubric --holdout-audit-root`, and `--verify`). The commands were written from `tools/render_corpus.py` argument parsing and corpus-v0 split rules. B: the 96-case development command and its `-S --verify` were executed by #19 (`development-corpus-first.json` `commands[]`, three commands, all exit 0) and repeated by #20; neither was re-run by this audit. The holdout command has never been run and was not authorized |
| Document storage requirements for the 128-case corpus | **established after this change** | Same new section: audio lower bounds 67,737,600 B (96) and 90,316,800 B (128) from 176,400 float32 samples per case, plus metadata, indexes, journals, staging, and filesystem requirements. These lower bounds are derived arithmetic. B: the #19 store was measured at 390 files and 71,820,533 B (`development-corpus-first.json` `storage`), consistent with the 96-case lower bound plus metadata, indexes, and journals; the #20 repeat store measured 71,818,190 B. Historical measurements, not re-measured here. No measured 128-case (holdout-inclusive) store exists |

## Integration checks (metadata, store, runner)

- Plan, index, and run relationships: established from the embedded smoke
  documents as listed under Executed checks and the smoke table (class B plus
  field comparisons). The same plan and index references appear in both runs.
- Stable two-case index and resume: fresh synthetic test (AC1) plus recorded
  identical index ref/sha256 across runs.
- Identity/provenance/collision boundaries: AC3, AC5, AC8 above.
- Verification without site packages: fresh synthetic tests passed in
  `test_corpus.py` and `test_storage.py`. The recorded `python3 -S --verify`
  command on the smoke store (exit 0 in `command_exit_codes[2]`) is historical,
  as is the real-scale `python3 -S --verify` of the #19 96-case run (exit 0 in
  `development-corpus-first.json` `commands[2]` and `verification[0]`).

## Bounded gaps and follow-ups

| ID | Gap | Why it remains | Bounded next step |
| --- | --- | --- | --- |
| G1 | Producer commit `224eb15a599171b3a8b65ca72457f7e452b83c8e` is not reachable from any branch: after `git fetch origin`, `git cat-file -t 224eb15a...` fails (exit 128) because `25495a3` squash-merged PR #110. **Narrowed 2026-10-07:** GitHub still serves it. `gh api repos/2AMLogic/gf180-torchsynth/commits/224eb15a...` returns the commit ("fix: bind every resume event to its original artifact reference", 2026-09-19), associated with PR #110 (`feature/issue-15`). `git fetch origin pull/110/head` (into `FETCH_HEAD` only, no ref created) made it a local `commit` object. It is an ancestor of the PR head `6c285a28ed2217c77fdfdc306ee6cb804841b768`, and `git diff --stat 224eb15 6c285a2` touches only `src/torchsynth_voice/corpus.py`, `tests/test_corpus.py`, and `sim/reference/corpus-smoke.json` | The source checkout is now obtainable, but this audit did not reproduce anything. No admitted runtime (Apple host with Docker amd64 emulation and the private exact upstream archive) is available on this dispatch worker, and the issue forbids a render made only to write this audit | On an admitted runtime, run `git fetch origin pull/110/head` and check out `224eb15a...` in an isolated worktree. Then run an independent reproduction of the two-case smoke into an isolated store. GitHub may eventually garbage-collect the PR ref. A durable ref (for example an annotated tag) would need an operator decision. This gap is specific to the two-case smoke producer: the 96-case development run (producer `bd8b35e4...`) already has an independent byte-repeat recorded by #20 (`development-corpus-repeat.json`, `COMPLETE-REPEAT`). Fresh runtime measurement: **not run** |
| G2 | Raw store bytes for run `6e198bc9a03f4a929ac0a59a93c775cf` are absent (`out/corpus-smoke-15` is ignored and not retained), so read-only `--verify` with envelope digest `122cd1cf...7818` could not be executed. Re-checked 2026-10-07: neither `corpus-smoke-15` nor the run directory exists anywhere on this worker's root filesystem | Embedded metadata cannot substitute for payload hashing; exact-byte digests are not reproducible from embedded canonical JSON | If the bytes are recovered, run `python3 -S tools/render_corpus.py --store <recovered> --verify 6e198bc9a03f4a929ac0a59a93c775cf --run-sha256 122cd1cf...7818` and record the result. Store verification: **not run** |
| G3 | **Narrowed 2026-10-10 (revision `74a74e7`).** Newly produced run receipts are now portable by construction and by validation, established at synthetic-test scope (see the `74a74e7` run section; #289 closed). **Remaining:** the retained historical records still embed absolute host paths and are unchanged. In `corpus-smoke.json`: Docker `--mount src=<host-worktree>/.loom/worktrees/issue-15` and `src=<tmp>/...` at `render_run.attempts[*].receipt.command[26]` and `[28]` (same in `resumed_run.attempts[*]`), plus `local_checks[0].command` and `limits[4]`. The `development-corpus-*.json` receipts also contain operator-home store paths: in `-first.json` at `commands[*].command`, `smoke.store_root`, `storage.root`, `verification[0].command`; in `-repeat.json` at `commands[*].command`, `pre_run_store_inventory.smoke_store`, `storage.root`. This audit quotes them only as placeholders | The #289 policy deliberately leaves retained evidence byte-identical and verifiable only via the explicit `historical-v1` mode; `spec/CORPUS-RUNNER.md` states that republishing historical evidence is a separate decision. The portable check covers attempt receipts only; the `local_checks`, `limits`, and `commands` text in the evidence JSONs is outside the run-envelope validator. The JSON schema itself still types `receipt` as a plain object. No production run under the new policy exists; the synthetic backend and tests used an injected renderer, not Docker | Decide whether to republish or annotate the retained evidence (separate decision, no change here), and confirm portable receipts in the first real render under the new policy. Fresh runtime measurement of that: **not run** |
| G4 | Real holdout behavior (the 32-case one-shot holdout command has never been run) and named-trace capture are not evidenced. The 96-case development corpus is not part of this gap: it is evidenced historically (class B) by #19 and #20 above | Holdout access is not authorized; traces are #7/#24 work. The real development runs requested no traces | Tracked by those issues; synthetic admission/refusal tests are the only holdout evidence. Re-checked 2026-10-10: the holdout seal (`e13c33b`, Part of #55) adds `tests/test_holdout_seal.py`, which this audit did not run, so it adds no executed evidence here |

No claim is made about holdout or full 128-case results, sound fidelity, scalar promotion,
gf180mcu synthesis, layout, signoff, or hardware playback.

## Re-running this audit

```sh
python3 -m unittest discover -s tests -p test_artifacts.py -v
python3 -m unittest discover -s tests -p test_storage.py -v
python3 -m unittest discover -s tests -p test_artifact_renderer.py -v
python3 -m unittest discover -s tests -p test_corpus.py -v
python3 tools/check_contract.py
```

Python 3.11 or newer is required. An unavailable interpreter makes each result
**not run**.
