# Independent float control path v1

`torchsynth_voice.control_path` implements the candidate independent float
model of the pinned Voice's control path for issue #40: keyboard, the six
ADSRs, the two LFOs, the two control VCAs, the 4x5 modulation matrix, and
the five shared endpoint-aligned control upsamplers. Ownership follows the
landed checkpoint map (`reference/float-checkpoints-v1.json`): orders 1-13
plus the remaining shared upsampler instances (orders 15, 17, 19, 22) - 22
named traces. VCOs, VCAs, noise, and normalization belong to #41/#42/#43.

The module is stdlib-only, imports no TorchSynth, Torch, or NumPy, and
performs no render against the pinned source. Interfaces and boundary
classes stay owned by `spec/FLOAT-INTERFACES.md` and
`torchsynth_voice.float_interfaces`; this document declares the model's
calculation policy, fixture set, reference-capture protocol, comparison
rubric, and mutation controls. It ratifies no numeric format: the
checkpoint map's `numeric_contract` remains `unbound:#53` and DR-0008
remains Proposed - none of its selected values is read, depended on, or
ratified here.

## Declared float calculation policy

| Aspect | Policy |
| --- | --- |
| Internal arithmetic | binary64 (Python floats), per-sample |
| Trace outputs | rounded to binary32 at every declared trace output; keyboard scalars are the consumed physical values rounded once through binary32 |
| Pinned constants | `pi = 3.1415927410125732` (the pinned binary32 pi), LFO selector exponent `2.718281828` (the pinned literal, not `math.e`), ADSR epsilon `1e-6` |
| ADSR timing | stage durations in seconds times the 441 Hz control rate, kept fractional; a zero-length stage is the all-one ramp exactly like the pinned binary32 division by zero |
| LFO phase | phase accumulates the first frequency increment before `initial_phase` is added (first-sample phase convention); the modulated rate clamps at zero before accumulation |
| LFO shapes | continuous blend `sum_k w_k^e * shape_k / sum_k w_k^e` over sin, tri, saw, rsaw, sqr; all-zero weights are refused as undefined (`UndefinedControlState`), never repaired |
| Mod matrix | plain 4x5 weighted sums over inputs (main ADSR1, main ADSR2, post-VCA LFO1, post-VCA LFO2) with **no clamp** anywhere |
| Upsampling | endpoint-aligned linear interpolation reading the exact rational source coordinate `j*(1764-1)/(176400-1)`; endpoints are exact copies of control indices 0 and 1763 |

The binary64-internal policy is not a claim of byte identity with the
binary32 reference (DR-0007); differences are decided per trace by the
preregistered rubric below.

## Physical seam

`physical.parameters` is class `measured-observation`. The model consumes
the observed runtime conversion verbatim: `ControlPathModel` refuses a
request whose physical map is missing, and the module implements no
parameter conversion at all, so an analytic substitution is structurally
impossible. Analytic fixtures are used only for isolated-module semantics
tests; they are never substituted for observed conversions in the pinned
match.

## Reference capture (preregistered)

`env/release-era/capture_control_path.py` runs inside the unchanged
release image (runtime profile `release-mkl-compatible-v1`, DR-0006) with
the pinned source gate and the landed passive capture
(`torchsynth_voice.trace_capture.TraceCapture`): observers return `None`;
graph code is never wrapped or replaced. Preregistered fixture set:

- every directed case of `reference/directed-voice-v1.json` (392 patches,
  batch-32 reproducible, patch frozen at slot 0, noise slot 0), and
- the first clean development corpus, global indices 0-95
  (`spec/DEVELOPMENT-CORPUS.md`), each rendered in its own reproducible
  batch with the global slot selected.

Per case the worker records the observed normalized and physical maps
(canonical-JSON digests), the selected noise stream bytes and digest
(`input.noise` is class `exact-bytes`), and the 22 control-path traces as
`f32le` files with SHA-256 digests. Raw bytes stay in the operator's
`out/` store (never committed); the committed evidence is digests and
measurement summaries only.

## Comparison and rubric

`tools/compare_control_path.py` (stdlib-only) renders the model from each
case's observed physical map and compares every owned trace with the
landed paired metrics (`torchsynth_voice.paired_metrics`), time-locked,
never aligned, trimmed, or gain-fitted. Boundary classes:

| Boundary | Class | Comparison |
| --- | --- | --- |
| `input.normalized`, `input.noise` | `exact-bytes` | digest equality: manifest record vs on-disk bytes vs resolved request |
| `physical.parameters` | `measured-observation` | canonical-JSON digest equality; consumption verified through the rendered keyboard scalars |
| 22 owned graph traces | `declared-metrics` | paired `max_abs_error` under the preregistered rubric |
| upsample endpoints | declared contract | first/last binary32 samples byte-equal to the control column on both reference and model (`trace_capture.check_endpoint_bytes` semantics) |

Rubric tolerances are preregistered in
[`reference/control-path-rubric-v1.json`](reference/control-path-rubric-v1.json)
after calibration on the full fixture set: the smallest power of two that
is at least 2x the measured per-trace maximum absolute error, per the
repo's measure-then-preregister calibration policy. Tolerances are never
increased after the rubric is committed; a miss is a mismatch, never a
tolerance problem. Calibration observed maxima are recorded inside the
rubric file for provenance. The rubric carries
`numeric_contract = "unbound:#53"` and decides nothing about any
fixed-point format.

## Mutation controls (must fail)

Every preregistered mutation must fail the declared match on its named
traces; a mutation that does not fail is a defect of the control, not a
pass:

| Mutation | Must fail on |
| --- | --- |
| `clamp-mod-matrix` (clamp matrix outputs to [-1, 1]) | `mod_matrix.*` |
| `selector-lfo` (discrete one-hot selector instead of the blend) | `lfo_*.raw` |
| `integer-adsr-timing` (integer-rounded stage samples instead of fractional) | `*_adsr*.output` envelopes |
| ZOH upsampling | `control_upsample.*` |
| `off-endpoint` coordinate (`j*1763/176400`, dropped endpoint) | `control_upsample.*` and the exact-endpoint contract |

The comparator runs the mutation set against the first 8 development
cases (fixed order); the committed evidence records per-case failing-trace
counts. A clamped matrix is ineffective on the default base patch (its
only nonzero depth routes a column that stays within [-1, 1]); the
render-level mutation gate therefore exercises it at depths that exceed
unity, and the pinned-match mutation set uses random development draws
where several depths are active.

## Localization

Every comparison row names its case and registry trace; `compare_case`
writes the raw paired-metrics diagnostic bytes (content identity
`pm1-<sha256>`) per case/trace, so any mismatch localizes to its named
module/trace with raw artifacts kept. Exact-byte digest refusals fire
before any comparison.

## Explicitly out of scope

VCO, VCA, noise, mixer, and normalization behavior (#41/#42/#43); any
fixed-point word width, scaling, rounding, or approximation (#53 / DR-0008,
Proposed); scalar-vs-batched equivalence claims (DR-0007); sound fidelity,
hardware, synthesis, layout, signoff, and playback claims of any kind.
