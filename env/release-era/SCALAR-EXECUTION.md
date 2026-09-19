# Resolved scalar execution probe

From a clean checkout with Docker BuildKit and a host `python3`:

```sh
./env/release-era/qualify_scalar.sh
./env/release-era/qualify_scalar.sh --sentinel out/scalar-sentinel
python3 -S env/release-era/qualify_scalar.py verify --output out/scalar-sentinel
python3 -S -m unittest discover -s env/release-era -p test_qualify_scalar.py -v
```

The wrapper builds the unchanged digest-pinned release-era Linux/amd64
Dockerfile and hash-locked packages. Each of two repetitions starts a fresh
canonical process and a fresh scalar process. Containers have no network,
one CPU, a 3 GiB memory limit and numerical thread counts of one. The source
and repository mounts are read-only. Output directories must be new; failed
runs retain their actual reports, stderr and any artifacts already written.

`scalar-cases.json` preregisters twelve cases, three sentinel cases and three
independent negative controls. Four cases are unmodified corpus rows: global
0, 6, 31 and 39942 (batch 1248, slot 6 at width 32). The other cases use
landed directed fixtures, assigning their **normalized binary32 values** by
canonical name. They are directed variants with corpus noise coordinates,
not unmodified corpus identities. No holdout is read. The normalization
case uses two flat envelope routes to the noise path; it makes no claim
about the directed manifest's separate exact peak-neighborhood targets.

For canonical generation, `SynthConfig(batch_size=32, reproducible=True)`
and `Voice(batch_idx)` retain the supported upstream randomization path.
Directed patches freeze all 78 normalized parameters before that call.
For scalar execution, `SynthConfig(batch_size=1, reproducible=False)` uses
the same graph, all 78 normalized values are loaded by name with exact
readback, and the selected canonical noise tensor is copied explicitly.
The scalar call is `Voice()` with no batch index. Physical conversion is
observed separately; neither `set_parameters` nor the fixture's analytic
physical values is used for replay.

`scalar-capture-v1` is a runner-local capture manifest, not the eventual
production trace adapter/registry owned by #22/#24. It observes 36 seams
in execution order, including the six envelopes, raw/post-VCA LFOs,
matrix outputs, upsampled controls, raw/post-VCA sources, pre-normalization
mix, peak and final audio. Forward hooks only read tensors and return
`None`. A Python return profiler observes the original normalization
function's local input and peak. Every case compares hooked audio to a
second unhooked render on each execution width.

`mixer.gain` is explicitly a **derived diagnostic reciprocal**, computed
as `1 / peak` when the peak exceeds one and unity otherwise. Upstream
normalization divides directly by peak; the probe does not replace that
division with multiplication or claim that a reciprocal is a graph seam.
`physical.parameters` is a separate conversion observation in sorted
canonical-name order. Keyboard scalars are represented as one sample;
all signal traces keep their original sample count and binary32 bytes.

The independent wrong-parameter and fresh-randomization controls must
refuse at `input.normalized`. The wrong-noise control retains the scalar
constructor's seed-13 slot-0 stream when replaying slot 6 and must refuse
at `input.noise`. These controls exit 1 before output comparison; final
audio cancellation cannot hide their incorrect inputs. They have separate
output directories and are never counted as genuine scalar observations.

The aggregate `scalar-execution.json` retains each original-byte comparison,
first divergent seam/sample/values, maximum and mean absolute error, RMS
error, source and definition hashes, exact packages/build configuration,
image and host identity, warnings, raw artifact hashes and repeat-report
hashes. Arithmetic for difference statistics uses Python binary64 without
alignment, trimming or rescaling. Signed-zero byte differences remain
differences even when numeric error is zero.

`status: PASS` means the apparatus, replay gates, completeness, repeat and
negative-control checks succeeded. **`byte_equivalence` is a separate
verdict.** Nonzero numerical drift is not byte equivalence. A missing seam,
unexpected branch, stale provenance, mismatched input, malformed artifact
or unrun runtime refuses qualification. The full checked-in measurement
is `sim/reference/scalar-execution.json`; raw tensors remain in the ignored
output directory and can be reproduced using the wrapper.

The committed full run measured byte equality at all 36 seams for all twelve
cases, in two fresh canonical/scalar process pairs. All differences are zero
and first divergence is null. These measurements are scoped to the recorded
release-era environment running under amd64 emulation on the arm64 host.
The report's independent control records retain the actual changed named
values and noise hashes along with the expected refusal seam.

The scoped decision is [DR-0007](../../spec/decision-records/0007-single-sound-execution.md).
Issue #12 owns runtime ratification and DR-0006. Root must reconcile the
measured runtime here with that selection before integration; this probe
does not establish a chosen-runtime single-lane float/fixed bridge.

CI also checks that its actual sentinel render reproduces the committed
selected case records and seam bytes. A changed expected artifact, parameter,
source or runtime refuses verification. Kernel/host identity is reported
but not required to match the measuring host; a different native/emulated
host that produces different bytes fails the sentinel and needs explicit
runtime-scope reconciliation. No record is updated automatically.
