# DR-0007: Separate resolved single-sound execution from corpus identity

- Status: Scoped diagnostic decision; chosen-runtime reconciliation pending #12
- Date: 2026-09-19
- Decision owners: 2AM Logic
- Evidence: [scalar-execution.json](../../sim/reference/scalar-execution.json)
- Reproduction: [SCALAR-EXECUTION.md](../../env/release-era/SCALAR-EXECUTION.md)

## Decision

Batch-size-1 PyTorch is a **diagnostic oracle under the measured release-era
runtime and explicitly recorded math environment**, not the normative corpus identity generator or a ratified
single-lane float/fixed bridge. It may replay a resolved patch only after
all 78 normalized binary32 values have been assigned and read back by
canonical name and the selected canonical noise samples have been copied
exactly. Invoke `Voice()` without a batch index using
`SynthConfig(batch_size=1, reproducible=False)`.

The normative source remains
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`, with the default Voice nebula,
four seconds, 44.1 kHz audio and 441 Hz controls. Canonical synth1B1
identity continues to resolve via supported reproducible batching; scalar
randomization and seed-13 slot-0 noise cannot substitute for those inputs.
Normalized values are authoritative inputs. Physical parameter conversion
is separately observed using the pinned implementation, not replaced by
analytic fixture values or a normalized-to-physical-to-normalized round trip.

The hardware boundary is **one resolved, name-keyed 78-parameter patch and
one resolved noise stream per render**, independent of Python batch size.
The multiple-of-32 restriction belongs to corpus randomization and the
canonical noise cache. It is not a requirement for 32 hardware lanes or
32 simultaneous clips. No hardware interface, arithmetic policy, noise
generator, conditional normalization expression or RTL is changed here.

## Evidence scope and interpretation

The committed probe uses the unchanged release-era lock: Linux/amd64,
Python 3.9.13, PyTorch 1.12.1+cpu, NumPy 1.23.2, SciPy 1.9.0 and Lightning
1.8.6. The measurement host is Darwin/arm64 with a Linux/arm64 Docker
server running the amd64 image under emulation. Numerical intra-op and
inter-op threads are one. Exact packages, build configuration, source,
image, commands and artifact hashes are retained in the evidence.

The revised diagnostic profile explicitly sets `MKL_CBWR=COMPATIBLE` before
Python starts and leaves `ATEN_CPU_CAPABILITY` unset. Both values are
verification inputs. This scoped experiment does not ratify a canonical
runtime or broaden the measured host set; DR-0006 / issue #12 still own
that integration decision.

The original full `qualify_scalar.sh` run recorded **byte equality for all twelve
cases at all 36 seams** in both fresh canonical/scalar process pairs.
Every first-divergence field is null; all maximum/mean/RMS differences
are zero. Both widths reproduced their own records exactly across fresh
processes, and all 48 per-case hooked/unhooked checks passed. This is the
measured outcome on this runtime/host, not a batch-1 identity guarantee.
That original report is preserved as historical evidence. Its process
freshness claim did not include per-execution identities and must not be
used as evidence for the strengthened aggregation protocol.

Native CI run `35415309295` failed the original committed-byte sentinel:
global-6 first differed at `lfo_1.raw` sample 2 (`0.5217112302780151` locally,
`0.5217111706733704` natively). Inputs, physical parameters and the four
preceding envelopes agreed. The original final audio max/RMS differences
were `0.0007759928703308105` / `0.000031622327713416255`; no tolerance is
introduced for them. Two directed sentinel cases also differed at the LFO
despite equal final audio.

An isolated LFO test on native CI run `35416191562` compared baseline and
`MKL_CBWR=COMPATIBLE`: frequency, phase argument, waveform shapes, weights
and output all agreed. The compatible local isolated output matched native
bytes, while local baseline differed only after the weighted reduction.
This justified measuring a separate compatible profile, not replacing the
source graph or declaring unrestricted cross-host determinism. Historical
bytes and the failed CI runs remain part of the record.

The revised compatible-profile full run measured all twelve cases locally
under amd64 emulation: every one of the 36 scalar/canonical seams was byte
exact in both fresh repetitions, and all three independently observed
controls refused at their specified input seam. Native CI run `35416530832`
then measured the three preregistered sentinel cases with the same explicit
environment. Both widths' complete case records matched the local run
exactly. The committed `profile_validation` retains the native runtime,
provenance, execution identities, report hashes and case-record hash; its
`historical_baseline` retains the complete original report. This is evidence
for twelve local cases and three native cases, not twelve native cases or
unrestricted portability. That intermediate CI run deliberately remained
red to preserve the uncontrolled baseline failure while measuring the new
profile; the final sentinel explicitly selects the compatible profile.

The explicit sine bypass peak was `0.24973656237125397`; the explicit
noise normalization peak was `1.9999518394470215`. The unmodified global-0
case also applied normalization (`1.8409372568130493` in the historical
baseline; `1.8409373760223389` in the compatible profile). The wrong-parameter
control failed at `input.normalized` for `keyboard.midi_f0`; the wrong-noise
control failed at `input.noise`; fresh scalar randomization failed at
`input.normalized` for `adsr_1.alpha`. Each returned exit code 1 as required.

Twelve preregistered cases cover three noise slots, unmodified global
indices 0, 6, 31 and 39942, sine/square/saw/blended oscillators, an LFO
waveform blend, zero and overlapping envelope stages, and demonstrably
bypassed/applied normalization. Directed cases preserve explicit noise
coordinates but are not labeled unmodified corpus sounds. The project's
holdout remains sealed; the upstream train/test identity flag is merely
retained for the explicitly authorized corpus probes.

The comparison includes 36 named observations in execution order. Six
envelopes, raw/gated LFOs, matrix and upsampled controls, raw/gated audio
sources, the pre-normalization mix, peak and final audio are captured
without changing upstream expressions. `mixer.gain` is a derived
diagnostic reciprocal, since upstream divides by the peak directly.
Hooked/unhooked checks establish instrumentation invariance for the
selected rows; fresh processes establish the reported repeat result.

Each seam retains original-byte equality and first differing sample/value,
maximum/mean absolute difference and RMS difference without alignment or
rescaling. The report separates apparatus `status` from `byte_equivalence`.
An input mismatch, missing seam, malformed artifact, stale source/runtime
provenance, unrun environment or failed repeat cannot become a pass.
Independent wrong-parameter, wrong-noise and fresh-randomization controls
refuse at their preregistered input seam before final audio can conceal
an error through cancellation.
The revised reports additionally bind execution UUID/PID/UTC, campaign and
repetition identities, scalar-to-canonical UUID/report hashes, and the actual
32/reproducible versus 1/nonreproducible configuration. All seven execution
UUIDs must differ. Control provenance/runtime and retained changed inputs
must match their associated canonical run and independent mutation; a
stale report or matching error-string prefix alone cannot pass aggregation.

The issue's earlier nonzero drift observations used a different current
runtime and remain exploratory. The release-era measurement does not
adjudicate them. The current macOS Python 3.13/PyTorch 2.14 environment
was explicitly refused by this scoped runner before rendering, with
`NO_VERDICT`; no scalar samples from that environment are qualified here.

## Consequences and integration gate

Keep batch-1 diagnostic despite bounded byte equality: finite development
cases do not prove equality for every patch, host, runtime or tensor shape.
CI executes a small real render and compares all selected seam bytes with
the committed record. A host producing different bytes fails that check;
it must not silently acquire a widened runtime scope or updated golden data.

Issue #12 owns canonical runtime selection and DR-0006. Before integration,
root must cross-check this record against that decision. If the selected
runtime differs, retain this record with its present scope and obtain
the corresponding scalar measurement before claiming that bridge is
qualified. A repeat failure or measured divergence must remain explicit;
do not relax byte equality into an arbitrary closeness threshold.

For downstream work, reference the selected canonical batched row until
that reconciliation is complete. Float-to-fixed comparisons still use
declared error metrics; fixed-to-RTL comparison remains sample-exact.
This decision establishes no sound fidelity, physical validation,
gf180mcu synthesis/layout/signoff, hardware playback or holdout result.
