# Resolved scalar execution probe

From a clean checkout with Docker BuildKit and a host `python3`:

```sh
./env/release-era/qualify_scalar.sh --mkl-compatible
./env/release-era/qualify_scalar.sh --sentinel --mkl-compatible out/scalar-sentinel
python3 -S env/release-era/qualify_scalar.py verify --output out/scalar-sentinel
python3 -S -m unittest discover -s env/release-era -p test_qualify_scalar.py -v
```

The wrapper builds the unchanged digest-pinned release-era Linux/amd64
Dockerfile and hash-locked packages. Each of two repetitions starts a fresh
canonical process and a fresh scalar process. Containers have no network,
one CPU, a 3 GiB memory limit and numerical thread counts of one. The source
and repository mounts are read-only. Output directories must be new; failed
runs retain their actual reports, stderr and any artifacts already written.

`--mkl-compatible` selects an explicitly bounded diagnostic math profile:
`MKL_CBWR=COMPATIBLE`, with `ATEN_CPU_CAPABILITY` unset, passed into Docker
before Python starts. Both environment values, including unset values, are
bound into runtime identity and sentinel verification. This does not select
the project's canonical runtime; issue #12 owns that decision. Omitting the
flag reproduces the original uncontrolled-MKL experiment and intentionally
does not match the compatible-profile sentinel contract.

Each process records a generated execution UUID, PID and UTC start time,
plus the wrapper's campaign UUID and repetition number. Container PIDs may
all be 1; UUIDs must differ across all four renders and three controls.
Scalar reports bind the exact canonical execution UUID and report hash.
Aggregation checks those associations, actual 32/reproducible versus
1/nonreproducible case configurations, and distinct artifact directories.
Duplicated reports, wrong sides, missing identities and stale repetitions
refuse even when their tensors agree.

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
Aggregation compares each control's source/runtime and expected input
records to canonical repetition 1. Retained actual noise bytes are hashed
and inspected: the wrong-noise control must keep normalized inputs exact
while changing the stream to slot 0; parameter controls must keep the
selected noise exact. Wrong-parameter must change only `keyboard.midi_f0`
to its opposite endpoint; fresh randomization must change multiple named
values. An expected-looking error string alone is insufficient evidence.

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

The original full measurement is retained as historical evidence in the
committed report. It measured byte equality between scalar and canonical
on the emulated host, but lacked execution identities and did not reproduce
all seam bytes on native CI. In run `35415309295`, global-6 first differed
at `lfo_1.raw` sample 2. Its final audio differed by maximum
`0.0007759928703308105` and RMS `0.000031622327713416255`.

The isolated global-6 LFO experiment in CI run `35416191562` found identical
frequency, argument, waveform shapes and weights across native baseline
and compatible runs. Their output hash was
`753d6ea6237580414e34c615719ecb139f8b6196ded8974406e086c6db6d627a`.
On local amd64 emulation, the uncontrolled output hash was
`7a6f585a3e8b68e7af29fea0c0385ede40da4700cf5fe8c08349847345412c4d`;
setting only `MKL_CBWR=COMPATIBLE` reproduced the native output. This
isolates a reduction-dispatch lead, not a claim of general portability.
The separate compatible-profile measurements retain the full graph,
selected seams, execution associations and independently observed controls.
The revised full run measured all twelve cases locally with exact equality
at every seam in both repetitions. Native CI run `35416530832` measured the
three sentinel cases; both widths' complete case records matched the local
measurement. The committed `profile_validation` contains native metadata,
execution/report hashes and an exact case-record digest; `historical_baseline`
retains the complete prior report. Raw native artifacts remain attached to
that CI run. The intermediate run intentionally stayed red for its preserved
baseline failure. Final CI selects the compatible profile explicitly and
requires its committed seam bytes; no tolerance or automatic refresh exists.

The scoped decision is [DR-0007](../../spec/decision-records/0007-single-sound-execution.md).
Issue #12 owns runtime ratification and DR-0006. Root must reconcile the
measured runtime here with that selection before integration; this probe
does not establish a chosen-runtime single-lane float/fixed bridge.

CI also checks that its actual sentinel render reproduces the committed
selected case records and seam bytes. A changed expected artifact, parameter,
source or runtime refuses verification. Kernel/host identity is reported
but not required to match the measuring host; a different native/emulated
host that produces different bytes fails the sentinel and needs explicit
runtime-scope reconciliation. No record is updated automatically. Historical
uncontrolled bytes are preserved when a separately named, explicitly
measured profile is recorded; they are not reinterpreted as compatible bytes.
