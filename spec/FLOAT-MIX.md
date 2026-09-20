# Independent float mix chain v1

This document declares the landed independent float implementations of the
audio VCA call sites, the pre-normalization mixer, and the conditional
whole-clip normalization of the default Voice — delivered for issue #42. It
consumes the interfaces and checkpoint map of
[FLOAT-INTERFACES.md](FLOAT-INTERFACES.md) unchanged and owns no boundary
types: `vca#1`/`vca#2`/`vca#3` (orders 16, 20, 23),
`normalize_if_clipping` entry/reduction/decision (orders 24–26), and the
`mixer#1` return (order 27) — the seven registry traces `vco_1.post_vca`,
`vco_2.post_vca`, `noise.post_vca`, `mixer.pre_normalization`, `mixer.peak`,
`mixer.gain`, and `mixer.output`. The control upsamplers feeding the VCA gain
envelopes belong to #40 and enter as declared audio-rate inputs; the sources
enter from #41 as declared buffers or resolved noise bytes; end-to-end
conformance belongs to #43.

## Ownership and policy

- Model module: `src/torchsynth_voice/float_mix.py`. Stdlib-only; imports no
  TorchSynth, Torch or NumPy; deterministic per render; no state across
  renders, so a repeated request reproduces its call log and outputs and a
  changed-inputs-then-restored sequence leaves no residue.
- Declared calculation policy (`float-mix-v1`): pinned-upstream op order at
  commit `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d` (`module.py`
  `VCA.output`/`Mixer.output`, `util.py` `normalize_if_clipping`).
  The VCA is the elementwise binary32 product `audio_in * control_in`; each
  product rounds once, which is the exact IEEE result of the pinned
  elementwise kernel. The mixer is the ordered weighted sum over
  `vco_1.post_vca`, `vco_2.post_vca`, `noise.post_vca` with the observed
  physical `mixer.vco_1`/`mixer.vco_2`/`mixer.noise` levels: products and the
  three-term accumulation run in binary64 in declared source order and round
  to binary32 once at the trace output. The pinned batched `matmul`
  accumulates in binary32 FMA chains and may differ by roughly one ulp.
- The mixer input curves `[1.0, 1.0, 0.025]` are recorded upstream facts that
  determine what the observed physical levels are; the
  normalized-to-physical conversion is never reimplemented in the model.
  `physical.parameters` stays class `measured-observation`: the model
  consumes the observed levels verbatim and an analytic substitution is
  structurally impossible.
- Normalization semantics: the whole-clip peak is the exact binary32 maximum
  of `torch.abs(signal)` over the complete clip; the recorded peak location
  is the earliest maximal index (pinned `torch.max` semantics). The branch
  condition is strict `peak > 1`: peak exactly one, silence, every peak at or
  below one, earliest-index ties and late peaks bypass with the input bytes
  unchanged; on the normalize branch the output is the elementwise binary32
  division of the mix by the observed peak, exactly like the pinned
  `torch.where` arm. `mixer.gain` is the derived diagnostic — one when the
  observed peak is at most one, otherwise its binary32 reciprocal — recorded
  for replay experiments and never used to generate audio: upstream applies
  division directly, and a multiply-by-reciprocal implementation is a later
  numerical choice that is not proof of byte-identical division.

## Comparison classes and declared limits

| Boundary | Class | Declared comparison |
| --- | --- | --- |
| `vco_*.post_vca`, `noise.post_vca` | `declared-metrics` | With bit-exact captured inputs the VCA product is IEEE-deterministic, so `tools/compare_float_mix.py` expects byte identity against the captured buffers; any observed difference is a mismatch, never a tolerance problem. |
| `mixer.pre_normalization`, `mixer.output` | `declared-metrics` | Paired `max_abs_error` against captured buffers, limited at capture-calibration time per the measure-then-preregister policy; byte identity is expected wherever the pinned GEMM did not fuse an FMA across the three-term reduction. |
| `mixer.peak` | `declared-metrics` | Exact scalar: the digest of the observed peak must equal the digest of the model's whole-clip maximum on the same input bytes. |
| `mixer.gain` | `derived-diagnostic` | The digest of `f32(1/peak)` on the normalize branch, `1.0` otherwise; verified against the committed capture digests below. |

The committed evidence for the normalization branches is
[`sim/reference/trace-capture.json`](../sim/reference/trace-capture.json):
the release-era capture recorded the three directed normalization cases with
peaks exactly on the directed binary32 targets — `normalization:above` at
`1.0 + 2^-23`, `normalization:below` at `1.0 - 2^-24`, `normalization:tie` at
exactly `1.0` — and gains matching the derived reciprocal rule
(`f32(1/peak)` above one, `1.0` at or below). The committed digest relations
show the bypass cases (`normalization:below`, `normalization:tie`,
`global-6`) preserving the mix bytes, the normalize cases
(`normalization:above`, `global-0`) altering them, and the tie case's mix
identical to its `vco_1.post_vca` lane. The record's `normalization_exercise`
additionally exercised at-boundary, late-peak, silence and tied-maximum
buffers inside the unchanged release image, all matching the strict
`peak > 1` rule. The landed release anchors (`spec/decision-records/0006-canonical-runtime.md:89`)
are `0.7895715833` and `3.9478583336`; the semantics fixtures use them as
declared scale anchors, one per branch.

`tools/compare_float_mix.py` (stdlib-only) verifies the committed digest
bindings store-free and, given the operator's store (`--store`), compares the
model chain — rendered from the captured inputs — against the captured
buffers for every owned trace, byte-exact for the IEEE-deterministic sites
and paired-metric for the two mix traces. Full-buffer calibration runs inside
the qualified release-era image; a test that has not run there is not
reported as run here.

## Mutation controls (must fail)

Every preregistered mutation must fail its named comparison rows; a mutation
that does not fail is a defect of the control, not a pass:

| Mutation | Must fail on |
| --- | --- |
| `limiter-substitute` (hard clip at ±1 instead of conditional normalization — a DR-0003-forbidden substitute) | `mixer.output` on an above-one clip |
| `agc-substitute` (constant headroom scale — a DR-0003-forbidden substitute) | `mixer.output` above one and the byte-preserving bypass row below one |
| `normalization-always-on` | the bypass rows below/at one and silence |
| `normalization-off` | the divided `mixer.output` row above one |
| `wrong-peak` (runner-up magnitude drives branch and division) | `mixer.output` |
| `wrong-mix-order` (sources presented against the reversed level order) | `mixer.pre_normalization`, `mixer.output`; never the post-VCA rows |
| `pre-vca-gain` (mixer level applied before the VCA) | all three `post_vca` rows and every downstream row |
| `latest-index-tie` | the peak-index diagnostic row only; peak value and output rows stay equal |

Gain, clipping, DC and silence are not normalized away by the tests: a
below-one clip must remain byte-identical, clipped samples above one must be
scaled (not left clipped, not clipped away), a DC offset keeps its value on
the bypass branch and scales exactly on the normalize branch, and silence
stays silence with unity gain and no division by zero.

## Explicitly deferred (numeric formats and replay stay open)

Fixed word widths, Q formats, LUT organization, rounding and saturation
sites, approximation budgets, and float/fixed tolerances are **not**
specified here; the checkpoint map carries
`numeric_contract = "unbound:#53"` and DR-0008 remains Proposed. DR-0003
remains Proposed: this work implements and tests the normalization
*semantics* only — peak selection, strict `peak > 1`, unity bypass, division
by the observed peak, and the derived reciprocal diagnostic suitable for a
deterministic replay experiment — and ratifies no replay/buffering hardware
architecture and no limiter, AGC or constant-headroom substitute. No
runtime, fixed-model, hardware, end-to-end or sound-fidelity claim is
established by this model or record; #43 owns end-to-end conformance.

## Verification

Bounded, honest checks executed for this document:

| Executed check | Observed result |
| --- | --- |
| `timeout 120 python3 -m compileall -q src tests tools` | recorded in the PR body |
| `timeout 600 python3 -m unittest discover -s tests -p test_float_mix.py -v` | 42 tests, all pass, recorded in the PR body |
| captured-digest bindings (exact directed targets, derived-gain rule, bypass/normalize byte relations) | enforced by `tests/test_float_mix.py` against `sim/reference/trace-capture.json` |
