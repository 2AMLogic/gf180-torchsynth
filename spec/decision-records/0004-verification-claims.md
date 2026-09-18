# DR-0004: Separate implementation identity from perceptual quality

- Status: Accepted
- Date: 2026-09-18
- Decision owners: 2AM Logic

## Decision

Verification uses a ladder of separately reported claims. There is no single
“TorchSynth similarity” number and no averaging of unlike units.

1. **Source identity:** prove exactly which upstream source, nebula, runtime,
   parameters, seed/noise, and configuration produced a reference.
2. **Reference repeatability:** the same identity reproduces parameter and
   float-audio hashes in the qualified environment.
3. **Algorithm fidelity:** compare named intermediate traces and final samples
   between the pinned Voice, transparent float decomposition, fixed model, and
   RTL. Fixed model to RTL is exact; float to fixed uses preregistered limits.
4. **Property fidelity:** independently measure pitch, modulation, envelopes,
   spectrum/noise, gain, clipping, timing, and normalization.
5. **Auditory transparency:** use calibrated perceptual metrics and blinded
   listening only as supporting evidence for fixed-point impairment.
6. **Distribution preservation:** use population metrics only to detect whether
   default-nebula output coverage drifted. They cannot establish patch identity.

An estimator must be qualified on known analytic signals and intentional
faults before it can issue a verdict. Insufficient evidence is `NO VERDICT`,
never a zero error or pass.

## Rationale

The TorchSynth paper tried several embedding distances and used OpenL3-L1 MMD
to tune nebulae. It also reported the critical negative result: the optimizer
exploited the metric, produced extreme pitches and unpleasant sounds, and
listeners consistently preferred the manually designed nebula. The paper calls
perceptually relevant metrics an open question.

This project has a stronger situation than generative-model evaluation: it owns
a deterministic executable reference and can compare the same named patch,
noise, and time index. A distribution distance can pass after permuting outputs
between sound identities, and a perceptual score can hide timing or gain defects
that violate the implementation contract.

The detailed metric and mutation plan is in
[`docs/MEASUREMENT-PLAN.md`](../../docs/MEASUREMENT-PLAN.md).

