# DR-0001: Pin a TorchSynth 1 Voice compatibility snapshot

- Status: Accepted
- Date: 2026-09-18
- Decision owners: 2AM Logic

## Decision

Implement the default `Voice` from TorchSynth commit
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`, on CPU, with the default
nebula. Call the target profile **TorchSynth 1 Voice compatibility**, not a
new Voice version.

Treat release `v1.0.2` (`115af79de4021408e05cc0785b9710e0982d0ce6`) as
the released compatibility baseline and TorchSynth 1.0 as the paper lineage.
The source commit, individual source files, nebula, runtime environment, and
generated fixtures are all separately versioned evidence.

## Why this commit rather than just a moving tag or `main`

The TorchSynth paper describes TorchSynth 1.0, and the project states that
the default settings of TorchSynth 1.x generate synth1B1. The default nebula
has the same SHA-256 at `v1.0.0`, `v1.0.2`, and the selected commit.

An audit on 2026-09-18 found:

- `v1.0.2`, published 2022-08-19, is still the newest release.
- The selected commit is the tip of `main`, dated 2024-10-07; no later commit
  had landed on `main`.
- Across the Voice implementation inputs pinned in
  `reference/upstream.json`, `v1.0.2..2b0964d` changes only
  `torchsynth/synth.py`: it imports `LightningModule` from `lightning` instead
  of the obsolete internal `pytorch_lightning.core.lightning` path. The DSP,
  parameter code, configuration, utilities, and default nebula are identical.
- Open pull requests are predominantly dependency upgrades. The open filter
  and parameter-API work is not merged and is outside this profile.

This gives the paper-era Voice graph and distribution while removing an
incidental import barrier. A raw version tag alone is not sufficient because
runtime libraries and CPU/GPU math can change generated samples.

## Constraints

- Never depend on the moving `main` ref.
- Do not take open PR behavior into the hardware contract.
- Render canonical fixtures on CPU. GPU output is a different, currently
  unqualified execution environment; upstream issue #256 documents observed
  CPU/GPU differences.
- Use canonical parameter names in serialized patches. Do not trust positional
  enumeration: upstream issue #435 documents inconsistent parameter ordering.
- Freeze sample rate, control rate, duration, dtype, library versions, platform,
  and artifact hashes with every reference run.
- A future TorchSynth patch is adopted only through another decision record
  and a corpus comparison.

## Remaining qualification

The canonical Python/PyTorch runtime is not selected by this record. The next
gate must render repeatable fixtures, compare a patched-import `v1.0.2` checkout
against this commit in one environment, and record cross-environment drift.

### Resolved 2026-09-19 by DR-0006 and DR-0007

That gate has since been run and decided, so this section is history rather
than open work. Nothing above it is superseded: the source pin, the released
baseline, the constraints and the CPU-only scope all still hold, and a future
TorchSynth patch is still adopted only through another decision record.

- **The canonical runtime** is named by
  [DR-0006](0006-canonical-runtime.md) — profile `release-mkl-compatible-v1`,
  CPython 3.9.13 / PyTorch 1.12.1+cpu / NumPy 1.23.2 / Lightning 1.8.6 on
  linux/amd64, built from `env/release-era/Dockerfile` and
  `env/release-era/requirements.lock`. DR-0006 supersedes **only** this
  section's open runtime question, and bounds its own portability claim in
  its `## Permitted host scope`.
- **Repeatable fixtures and cross-environment drift** are measured in
  [`sim/reference/repeatability-runtime.json`](../../sim/reference/repeatability-runtime.json):
  128/128 cells rendered, 64/64 fresh-process repeats and 48/48 batch-size
  comparisons byte-exact, and 32/32 cross-runtime comparisons retained as
  measured `FAIL`s rather than reconciled away.
- **The patched-import `v1.0.2` comparison** is
  [`env/release-era/source-comparison.json`](../../env/release-era/source-comparison.json)
  and its rendered counterpart
  [`sim/reference/source-equivalence.json`](../../sim/reference/source-equivalence.json):
  of the eleven pinned Voice files, only `synth.py` (the one-line
  `LightningModule` import, plus two docstrings) and the unused `profile.py`
  profiling CLI differ.
- **Batch-size-1 execution** is scoped separately by
  [DR-0007](0007-single-sound-execution.md) as a diagnostic oracle, not as the
  corpus identity generator; it reconciled against DR-0006 on 2026-09-19.

The consolidated cross-check of those records against each other — and the
program that re-runs it in CI — is
[`spec/CANONICAL-REFERENCE-QUALIFICATION.md`](../CANONICAL-REFERENCE-QUALIFICATION.md).

## Sources

- [One Billion Audio Sounds from GPU-enabled Modular Synthesis](https://arxiv.org/html/2104.12922v2)
- [synth1B1 reproducibility contract](https://torchsynth.readthedocs.io/en/latest/reproducibility/synth1B1.html)
- [TorchSynth v1.0.2 release](https://github.com/torchsynth/torchsynth/releases/tag/v1.0.2)
- [CPU/GPU reproducibility issue #256](https://github.com/torchsynth/torchsynth/issues/256)
- [parameter ordering issue #435](https://github.com/torchsynth/torchsynth/issues/435)

