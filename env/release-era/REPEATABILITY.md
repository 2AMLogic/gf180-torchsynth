# Runtime repeatability qualification

The preregistration is [repeatability-matrix.json](repeatability-matrix.json),
committed as `535c25c` before the first measurement. It names eight cases,
four batch sizes, two fresh-process repeats, and two locked CPU environments:
128 render cells. Six cases are unmodified default-nebula global identities;
two use global 0 with explicit physical mixer levels 0.2 or 1.0. These are
directed normalization controls, not new synth1B1 indices or holdout data.

## Reproduction

Use the committed locks without updating them. To create an isolated current
environment in this worktree, use `uv sync --locked --extra reference --python
3.13.2`; do not install into another worker's environment. The current-runtime
matrix requires the preregistered macOS/arm64 stack. A different stack must be
reported separately, not silently substituted.

```sh
bash env/release-era/qualify_repeatability.sh matrix \
  --current-python "$PWD/.venv/bin/python" \
  --source-root /tmp/torchsynth-review-20260918 \
  --output out/repeatability-new

# Exactly the bounded render/comparison command run by the CI workflow:
bash env/release-era/qualify_repeatability.sh sentinel --output out/ci-sentinel

python3 -S -m unittest discover -s env/release-era -q
TORCHSYNTH_ROOT=/tmp/torchsynth-review-20260918 PYTHONPATH=src \
  python3 -S -m unittest discover -s tests -q
TORCHSYNTH_ROOT=/tmp/torchsynth-review-20260918 \
  python3 tools/check_contract.py --require-git-commit
```

Use a new output directory for each run; the controller refuses to reuse one.
The shell builds the unchanged hash-locked release image and retains build
diagnostics in `out/repeatability-build.*/`. Each offline release worker has
one CPU and a 6 GiB memory limit. All BLAS and Torch thread counts are one.
The current worker uses the supplied Python read-only. Each batch/repeat
launches a fresh process and each case constructs a fresh Voice. No render is
reused as a repeat. The per-case report is written incrementally so resource
failures preserve completed observations; missing cells become `NO_VERDICT`.

`preregistered-plan.json`, commands, exact image identity, package inventories,
CPU/platform/Torch build, thread settings, warnings, stderr, process execution
IDs, timings, source hashes, named parameter maps, and raw-file hashes remain
in the output directory. Each raw file is contiguous little-endian IEEE754
float32. Normalized and actual physical parameters use separate arrays ordered
by 78 sorted canonical names. The observed train/test label is one byte.
Raw audio is 176400 samples; control traces are 1764 samples. Nothing is
aligned, trimmed, resampled, or level-normalized after Voice returns.

## Observation and comparison boundaries

Source hashes are validated before importing Torch or TorchSynth, including
the additional package inputs from the landed source-comparison manifest.
The imported synth path is checked. No upstream source, DSP expression, or
normalization function is replaced. Forward hooks observe ADSR 1/2, LFO 1/2,
VCO 1/2 and noise outputs. A call profiler clones the actual argument entering
`normalize_if_clipping`; the returned audio is also captured. The selected
noise is checked against the module's stream at `index % 32`.

The normalization probes require the selected pre-normalization peak to be
in `(0, 1]` or above 1 respectively. Global 0 at batch 32 additionally constructs
an unhooked Voice and byte-compares its audio, in both runtimes and repeats.
These nine seams are a bounded qualification probe. They do not substitute for
the future production trace registry/adapter.

Each repeat and each non-32 batch is compared with its same-runtime batch-32
reference. Cross-runtime pairs use the same batch coordinates and inputs.
The comparator first checks original bytes (including signed zero), then
reports first differing byte/sample and values, maximum absolute, mean
absolute, and RMS differences in binary64. Numerical equality alone does
not establish original-byte equality.

The committed [machine-readable evidence](../../sim/reference/repeatability-runtime.json)
retains all cell outcomes and comparisons. `PASS` on a render cell means its
shape/input checks ran; only the separate repeat/batch comparison can establish
identity. A measured disagreement is `FAIL`; an unavailable cell is
`NO_VERDICT`. Cross-runtime `FAIL` preserves measured drift and does not change
the same-runtime qualification verdict. No case is a hardware fidelity,
physical implementation, scalar-execution, or holdout qualification claim.

The controller writes the complete record as `repeatability-runtime.json`
and a bounded `publication.json` for review/commit. The latter omits repeated
parameter maps and input descriptions, retaining their byte hashes, the
shared sorted `parameter_names`, the full metrics and a hash of the complete
raw report. A parameter metric's first differing sample indexes that sorted
name list. All original maps and raw float32 files remain in the local run
directory. These are regenerable artifacts; no audio or holdout payload is
checked in.

The committed record additionally embeds `sentinel_execution`, the actual
post-matrix sentinel receipt (including its raw-report hash and mutation
outcomes). Its expectations are the unchanged top-level `sentinel` values.
The image IDs for separate builds can differ when runner/documentation files
change; the pinned base, source, package lock and checked package identities
remain the runtime definition.

The recorded matrix completed all 128 cells, all 64 repeat comparisons and
all 48 within-runtime batch comparisons successfully. All 32 cross-runtime
comparisons fail exact identity, as reported in DR-0006. Normalized parameters,
selected noise and labels match; physical transforms and DSP traces can drift.
This establishes the measured cases, not exhaustive invariance over all indices.

## Sentinel

The workflow builds the pinned release image and actually renders global 0,
batch 32, including the passive-capture control. It checks the preregistration,
input, label and all parameter/trace/audio hashes against committed expectations.
It also changes the expected input and audio hashes and requires their rejection.
A separate fresh process receives a copied source tree with a changed
`config.py`; it must reject the source before importing Torch. Failure to run
is a failing job, never a skipped/passed drift test. CI saves the raw render and
diagnostics even on failure.

The bounded sentinel is evidence for that case on the CI host. Passing it does
not qualify a new CPU for the full matrix. See
[DR-0006](../../spec/decision-records/0006-canonical-runtime.md) for host scope
and the policy for drift or a host that cannot reproduce the reference bytes.
