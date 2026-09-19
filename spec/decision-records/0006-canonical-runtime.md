# DR-0006: Canonical release-era CPU reference runtime

- Status: Conditional acceptance; effective on reviewed merge
- Date: 2026-09-19
- Decision owners: 2AM Logic
- Scope: Runtime qualification; supersedes only DR-0001's unresolved runtime choice

## Decision

### Candidate profile preregistration, before the Doctor remeasurement

The initial profile omitted explicit math-dispatch settings. Native CI run
35415961204 failed global-0 bytes; normalized/physical parameters, noise and
ADSRs matched, with the first captured numeric divergence in LFOs. The original
128-cell baseline and its 32 cross-runtime FAIL results must remain evidence.

Preregister `release-mkl-compatible-v1`: `MKL_CBWR=COMPATIBLE` and
`ATEN_CPU_CAPABILITY` explicitly unset, with unchanged source, image recipe,
package locks, threads, cases, counts and exact-byte rules. The current-runtime
comparator explicitly unsets both variables. These settings are supplied before
Python starts and recorded, never selected from host detection or inheritance.
This candidate is motivated by the isolated LFO reduction diagnosis and PR100
run 35416530832 at 86e7f45be378ccd1c18ab5154f605dab48f7a603: the explicit
COMPATIBLE three-case scalar/canonical sentinel step passed on native Linux;
the workflow deliberately retained its earlier baseline failure. This is
bounded supporting evidence, not native twelve-case/full-matrix qualification.

Rerun all 128 cells and compare them with the retained original raw bytes,
without alignment or level normalization. Require complete artifacts, exact
counts/dtypes/name maps and source/process/input bindings before comparison
credit. Only successful measured repeats/batches and a native actual-render
sentinel can support the proposed profile. Reviewed merge remains the
ratification gate; no broader host portability is assumed.

Use the unchanged `env/release-era/Dockerfile` and `requirements.lock` to
generate canonical floating reference fixtures. This selects CPython 3.9.13,
PyTorch 1.12.1+cpu, NumPy 1.23.2 and Lightning 1.8.6 on Linux/amd64, CPU float32,
with one Torch intra-op/inter-op thread and one thread for each declared BLAS
backend. The base-image digest, wheel hashes, source archive and lock are
normative; a mutable image tag is not an environment identity.

The selected source remains
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`. The default nebula, four-second
one-shot duration, 44100 Hz audio, 441 Hz control, seed-13 noise, parameter
names/order policy and upstream normalization behavior remain as specified.
Reproducible batch sizes are multiples of 32; this experiment measures 32,
64, 128 and 256. This says nothing about hardware execution width. Scalar
execution and its separate qualification decision belong to #88 / DR-0007.

## Permitted host scope

Full-matrix qualification is limited to the measured host:

- Apple M5, macOS 26.5.1 arm64;
- Docker server 29.7.2 on Linux/arm64, running the explicit Linux/amd64 image;
- observed container Linux 7.0.12-linuxkit, glibc 2.31, x86_64;
- observed emulated CPU `VirtualApple @ 2.50GHz`, family 6/model 142, with
  the complete CPU and Torch build identity retained in the evidence;
- one CPU per release container, 6 GiB memory limit, all declared threads one.

This deliberately permits the measured emulated execution profile. Native ARM,
GPU, other CPU feature sets, other OS/Python/package versions, and additional
batch sizes are unqualified. The native Linux/amd64 GitHub CI host runs a
bounded sentinel as an additional observation, not as full host qualification.
A passing sentinel permits that job to attest only its tested global-0 case.
It does not authorize production corpus generation on a new host.

## Evidence and rationale

The [preregistered matrix](../../env/release-era/repeatability-matrix.json)
was committed before measurements. The
[execution record](../../sim/reference/repeatability-runtime.json) and
[reproduction procedure](../../env/release-era/REPEATABILITY.md) distinguish
render validity, repeat comparisons, batch comparisons, cross-runtime drift,
and refusal states. The six indices exercise slot and train/test boundaries;
the two directed mixer settings exercise both normalization branches.
Observation-only hooks/profile seams preserve the unmodified Voice output,
checked with a separately constructed unhooked Voice.

| Observation | Measured outcome |
| --- | --- |
| Render cells | 128/128 completed; no refused, missing or failed cells |
| Fresh-process repeat comparisons | 64/64 byte-exact PASS |
| Within-runtime 32 versus 64/128/256 comparisons | 48/48 byte-exact PASS |
| Cross-runtime comparisons | 32/32 FAIL exact identity; disagreements preserved |
| Passive capture controls | Four byte-exact PASS controls (two repeats per runtime) |
| Release normalization controls | Pre-normalization peaks 0.7895715833 and 3.9478583336 |

Every comparison covers normalized and actual physical name-keyed parameter
arrays, selected noise, train/test label, named traces and raw audio. All 78
normalized parameters, selected noise and labels agree across runtimes for
the unmodified identities. Physical parameter transforms and DSP can differ.
For example, global 39942 has maximum absolute audio difference
`0.002283453941345215` and RMS difference `0.00031970997906043786`;
its first differing sample is 1. Global 0 has maximum absolute difference
`0.00018268823623657227`, RMS `8.418629731837671e-7`, first sample 46.
The complete per-seam first/max/mean/RMS measurements remain in the record.

These successful same-runtime comparisons satisfy the measured batch
invariant for the preregistered cases. They are not exhaustive proof over
every possible index. Cross-runtime failure is resolved by selecting one
canonical stack and retaining the other as a separately identified comparator;
no cross-runtime byte-identity claim or relaxed equality threshold is adopted.

The contemporary comparator is the unchanged `uv.lock` environment on native
Apple M5/macOS, Python 3.13.2, Torch 2.14.0, NumPy 2.5.3 and Lightning 2.6.6.
This comparison changes both runtime and host architecture; it cannot assign
causality to one library upgrade. Its disagreement is retained without time
alignment or level normalization. Neither environment's output is silently
substituted for the other's.

The release-era stack is selected for reference provenance closest to the
released Voice baseline, with the landed selected-source/release-source
equivalence experiment as supporting evidence. Repeatability alone does not
make one runtime more perceptually faithful. This decision makes no hardware,
sound-fidelity, physical-validation, batch-1 identity or holdout claim.

## Drift policy

The sentinel performs a real render and compares committed parameter, noise,
trace and audio hashes. Source, expected input and expected audio mutations
must be rejected. A schema-only record check is insufficient.

Any failed same-runtime repeat or batch comparison is a qualification failure.
An absent, crashed, timed-out or resource-refused cell is `NO_VERDICT`, not
proof of size independence. Preserve the failed/refused record and stop using
that runtime/host for new canonical evidence pending a documented resolution.
Do not loosen the identity assertions or replace the expected hashes to make
a failing run pass.

A changed source, lock, runtime, thread configuration or host requires a new
recorded experiment against the existing canonical bytes. Promoting that
change requires a reviewed decision record with raw, unaligned drift metrics;
a new source target also requires the source-contract decision process.
The committed expectations are versioned evidence, never auto-regenerated in
CI. Other runtimes may be used for explicitly noncanonical experiments.

## Ratification

The implementation and measured decision are submitted together for Loom
review. Reviewed merge ratifies this record; the Builder does not approve or
merge its own PR. Until then the canonical-runtime choice is proposed, and
consumers must not describe the PR's decision as already ratified.
