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
scope, but real-render evidence is historical only and four bounded gaps
remain (G1-G4 below). Issue #4 must stay open; this audit does not close it.**

## Evidence classes

| Class | Meaning in this audit |
| --- | --- |
| A. Fresh synthetic tests | Executed by this audit at revision `e29a8a946f564cbea4b0c318f0a86af1e3baa0e0` (see Executed checks) |
| B. Historical publication inspection | `sim/reference/corpus-smoke.json` read and cross-checked, not re-executed |
| C. Fresh runtime measurement | None. No Docker/amd64 render, store verification, or reproduction was run |

## Executed checks (class A)

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
`/Users/joseph/dev/torchsynth` checkout at `304824db...` was refused; bounded
software rendering and storage only.

Observed in the record: `raw_audio_committed=false`,
`raw_store_relative="out/corpus-smoke-15"` (git-ignored by `.gitignore`
`out/`). Real production payloads are therefore unavailable.

## Original acceptance criteria

| # | Original criterion | Verdict | Evidence and limits |
| --- | --- | --- | --- |
| AC1 | A two-case test corpus renders, validates, resumes without recomputation, and produces a stable index | **established** (synthetic, fresh) and corroborated by historical record | A: `tests/test_corpus.py::test_two_case_resume_zero_recomputation_and_stable_index` passed (backend call count unchanged on resume, byte-identical index). B: smoke record shows 2 cases, resume attempt count 2 unchanged, 0 additional render attempts, same index sha256 `964dbda2...308b` in both runs. Limit: the real render and real exact-byte verification were not re-executed or re-verified (see G1, G2); recorded PASS is not a test run here |
| AC2 | Interrupted/staging artifacts cannot be mistaken for complete artifacts | **established** (synthetic, fresh) | A: storage `test_process_exit_during_copy_leaves_only_incomplete_private_stage`, `test_process_exit_before_rename_is_not_discoverable_and_retry_succeeds`, `test_failure_after_atomic_rename_can_resume_complete_bytes`, `test_exception_cleanup_only_removes_own_staging`; corpus `test_interrupted_attempt_retained_on_resume`, `test_interrupted_index_publication_resumes_without_rendering`, `test_interrupted_reuse_retains_completed_artifact_without_rendering`. Limit: process interruption only; power-loss durability and network filesystems unqualified (`spec/ARTIFACT-STORAGE.md`) |
| AC3 | A changed source hash, runtime identity, lock, sample count, or audio hash produces a hard failure | **established** (synthetic, fresh) | A: renderer `test_request_mismatch_before_backend_access` (source commit, torch version, duration, batch size refuse before backend access) and `test_worker_source_gate_precedes_numerical_import`; artifacts `test_stale_content_id_is_rejected` (changed `runtime.lock_sha256`), `test_cross_artifact_checks` (index lock/fixture mismatch), `test_every_input_leaf_affects_identity`; renderer `test_invalid_observations_never_publish` (short audio, wrong noise hash); storage `test_missing_extra_truncated_corrupt_files_and_empty_directories`; corpus `test_corrupt_extra_missing_metadata_and_payload_refused` and `test_stale_resume_refused_before_renderer` (changed `project_git.commit`). Limit: coverage is by representative mutations and the leaf-identity test; no test mutates every source file hash individually against the real worker |
| AC4 | Reordered parameter arrays do not change name-keyed patch meaning | **established** (synthetic, fresh) | A: artifacts `test_key_order_is_irrelevant` (reversed `physical_by_name` and top-level key order give identical `content_id`), `test_diagnostic_orders_are_permutations_only` (orders are diagnostic only), `test_rejects_positional_missing_extra_and_mismatched_parameters`, `test_all_canonical_parameter_names_maps_locks_and_orders`. B: both smoke artifacts carry 78 name-keyed normalized and physical values. Limit: no real TorchSynth reordering was exercised |
| AC5 | Duplicate identities with conflicting bytes are rejected | **established** (synthetic, fresh) | A: storage `test_collision_reports_both_metadata_and_changed_output_hashes`, `test_metadata_whitespace_only_change_is_a_collision`, `test_two_process_writers_converge_or_report_collision`; renderer `test_same_id_different_bytes_collision_preserves_original`; artifacts `test_duplicate_case_or_sound_or_artifact_is_rejected`. Limit: cooperating local POSIX writers only |
| AC6 | Holdout artifacts have an access boundary documented and tested | **established** (synthetic, fresh; documentation inspected) | Documented in `spec/CORPUS-RUNNER.md` "One-shot holdout gate". A: corpus `test_default_has_only_development`, `test_mixed_holdout_refused`, `test_mixed_range_refused_before_any_renderer_or_store_access`, `test_holdout_admission_one_shot_and_same_run_resume_synthetic_only`, `test_interrupted_holdout_reuse_retains_one_shot_admission`. B: `actual_holdout_access=false`, `plan.admission=null`. Limit: synthetic payloads only; operator-attested freeze and operator-held ledger, no remote admission service; no real holdout rendered or accessed, and none was authorized |
| AC7 | Unit tests cover resume, collision, partial-write, corrupt-hash, missing-sample, and dirty-tree metadata | **established** (fresh) | Resume: AC1 tests. Collision: AC5. Partial write: AC2. Corrupt hash: storage corrupt/truncated test, corpus corrupt payload test, `test_corrupt_existing_artifact_never_resumes_or_gets_replaced`. Missing sample: storage missing/truncated files, renderer short audio, schema sample count of 176,400. Dirty tree: `test_artifacts.py::test_clean_and_dirty_git_digests`, `test_missing_provenance_never_defaults`. Limit: "missing-sample" is covered as missing/short audio payload and sample-count validation, not as a distinct sample-gap test |
| AC8 | The public metadata schema contains no absolute user path | **established** for artifact, index, and plan documents; **see G3** for run receipts | A: `tests/fixtures/artifacts/rejections.json` unsafe-reference mutations (POSIX/Windows/UNC/parent-traversal) all rejected by `test_negative_fixture_mutations`; `test_path_traversal_aliases_and_reserved_metadata_are_rejected`. A (script): no `/Users`, `/home`, `/private` in embedded smoke `artifacts`, `plan`, `index`. Limit: the strings in `rejections.json` are intentional negative inputs. The retained run `attempts[].receipt.command` strings and `local_checks`/`limits` text do contain host paths (G3) |

## Evidence requirement (original "Evidence" section)

| Requirement | Verdict | Evidence and limits |
| --- | --- | --- |
| Commit a tiny synthetic fixture/index suitable for CI | **established** | `tests/fixtures/artifacts/complete.json` (8,587 B), `index.json` (1,044 B), `rejections.json` (6,406 B), consumed by `tests/test_artifacts.py`, which passed here. Synthetic only; no sound-fidelity meaning |
| Keep full float audio out of Git unless explicitly decided | **established** | No audio files are committed; smoke record has `raw_audio_committed=false`; the smoke store path is under git-ignored `out/`. Storage tests generate zero-filled audio in temporary directories |
| Document exact commands for the 128-case corpus | **established after this change; not executed at scale** | Before this change `spec/CORPUS-RUNNER.md` gave only 0/1 development commands and a 96-case default, with no holdout invocation. This PR adds the "Full 128-case corpus commands and storage" section (96 development run, separate 32-case one-shot holdout run with `--holdout-once --frozen-rubric --holdout-audit-root`, and `--verify`). The commands were written from `tools/render_corpus.py` argument parsing and corpus-v0 split rules; neither full-scale command was run |
| Document storage requirements for the 128-case corpus | **established after this change** | Same new section: audio lower bounds 67,737,600 B (96) and 90,316,800 B (128) from 176,400 float32 samples per case, plus metadata, indexes, journals, staging, and filesystem requirements. Derived arithmetic, not a measured store size. `spec/DEVELOPMENT-CORPUS.md` separately states about 69 MiB of raw audio for the 96-case run, which is a different (recorded) figure not re-measured here |

## Integration checks (metadata, store, runner)

- Plan, index, and run relationships: established from the embedded smoke
  documents as listed under Executed checks and the smoke table (class B plus
  field comparisons). The same plan and index references appear in both runs.
- Stable two-case index and resume: fresh synthetic test (AC1) plus recorded
  identical index ref/sha256 across runs.
- Identity/provenance/collision boundaries: AC3, AC5, AC8 above.
- Verification without site packages: fresh synthetic tests passed in
  `test_corpus.py` and `test_storage.py`. The recorded `python3 -S --verify`
  command on the smoke store (exit 0 in `command_exit_codes[2]`) is historical.

## Bounded gaps and follow-ups

| ID | Gap | Why it remains | Bounded next step |
| --- | --- | --- | --- |
| G1 | Producer commit `224eb15a599171b3a8b65ca72457f7e452b83c8e` is not reachable in this clone: `git cat-file -t 224eb15a...` fails with `bad object` after `git fetch origin` (the evidence commit `25495a3` and later main are present) | The record says independent repeat must use the clean producer commit, and that checkout is unavailable here; no admitted runtime (Apple host with Docker amd64 emulation) was used either | Locate or restore the producer commit in a reachable ref, then run issue #20 style independent reproduction on an admitted runtime into an isolated store. Fresh runtime measurement: **not run** |
| G2 | Raw store bytes for run `6e198bc9a03f4a929ac0a59a93c775cf` are absent (`out/corpus-smoke-15` is ignored and not retained), so read-only `--verify` with envelope digest `122cd1cf...7818` could not be executed | Embedded metadata cannot substitute for payload hashing; exact-byte digests are not reproducible from embedded canonical JSON | If the bytes are recovered, run `python3 -S tools/render_corpus.py --store <recovered> --verify 6e198bc9a03f4a929ac0a59a93c775cf --run-sha256 122cd1cf...7818` and record the result. Store verification: **not run** |
| G3 | Retained run receipts embed absolute host paths (for example Docker `--mount src=/Users/joseph/dev/gf180-torchsynth/.loom/worktrees/issue-15` and `/private/tmp/...` in `render_run.attempts[].receipt.command`), contradicting the intent of "no absolute user path" for any retained public record, although artifact/index/plan metadata are clean | AC8 is scoped to the metadata schema; run-envelope receipts were not covered by a path-rejection test, and no committed test asserts receipts are path-free | Decide whether `corpus-run-v1` receipts should be path-normalized (or scrubbed in published evidence) and add a test; the smoke JSON is read-only for this scope |
| G4 | Full 96-case development corpus, real holdout behavior, and named-trace capture are not evidenced | Explicit non-goals here: #19/#6 (full corpus), #7/#24 (traces); holdout access is not authorized | Tracked by those issues; synthetic admission/refusal tests are the only holdout evidence |

No claim is made about full-corpus results, sound fidelity, scalar promotion,
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
