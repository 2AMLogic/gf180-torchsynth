# Roadmap and dependency map

Verification is the product. Each milestone produces reusable evidence before
the next implementation layer is allowed to obscure it.

The executable issue DAG lives in [Epic #1](https://github.com/2AMLogic/gf180-torchsynth/issues/1)
and [Epic #2](https://github.com/2AMLogic/gf180-torchsynth/issues/2). This
document explains the gates; the issues own builder-sized deliverables and
dependencies.

The scheduling DAG is not the evidence graph. The planned capability DAG
derives claim status from current checks, artifacts, controls, and covered-input
hashes; the scorecard reports case-level coverage and agreement. Their design
and the module-by-module debug order are in
[piecewise bring-up and evidence architecture](BRINGUP-AND-EVIDENCE.md).

```text
version/profile contract
        |
        v
reference environment + reproducible corpus
        |
        +--------------------+
        v                    v
traceable float model   qualified estimators + mutations
        |                    |
        +---------+----------+
                  v
       fixed-point feasibility + numeric DR
                  |
                  v
         fixed model (golden vectors)
                  |
          +-------+-------+
          v               v
      one-shot RTL     FPGA feasibility
          |
          v
     gf180 synthesis/physical feasibility
          |
          v
       sound-explorer integration
```

## Milestone 0 — contract and method

- Pin source, profile, identity, and source hashes.
- Audit releases, relevant upstream issues, and pending PRs.
- Define claim layers, corpus split, provenance, estimator qualification, and
  mutation expectations.
- Record unresolved boundaries as proposed decisions.

Exit: repository checks prove the manifests are internally consistent. This
does not yet prove an audio render.

## Milestone 1 — qualified reference corpus

- Select and lock a supported CPU Python/PyTorch/Lightning environment.
- Render the preregistered 96-case development corpus twice and prove repeat.
- Compare selected snapshot against `v1.0.2` with only the import compatibility
  adjustment.
- Qualify one-sound execution with a resolved named patch and explicitly
  selected noise stream against the canonical batched reference. Treat Python
  batch shape as an implementation detail whose numeric drift is measured.
- Capture parameters, noise identity, audio, traces, and full provenance.
- Keep 32 holdout cases sealed until the rubric is frozen.

Exit: replayable corpus with immutable hashes and an honest portability report.

## Milestone 2 — transparent model and measurement rig

- Re-express each Voice module in a traceable float model.
- Match named traces against the pinned reference.
- Implement and qualify property estimators on analytic fixtures.
- Implement the negative-control mutation matrix.
- Preregister directed edge/stress fixtures.

Exit: every required estimator has known validity bounds and every named fault
is detected by a required row.

Signal preparation is qualified separately from the estimators it feeds.
Applicable invariants must detect asymmetric windows, shifts, gain treatment,
and resampling errors without relying only on known-answer grids.

## Milestone 3 — fixed-point feasibility

- Sweep widths, rounding, saturation, interpolation, oscillator/envelope
  approximations, reciprocal, and state precision.
- Report errors per trace/property and preserve raw artifacts.
- Run calibrated auditory experiments only after signal-level correctness.
- Ratify arithmetic, thresholds, and the normalization boundary in new DRs.

Exit: frozen fixed model and rubric, then one blind holdout run.

## Milestone 4 — implementation

- Build one-shot RTL against exact fixed-model vectors.
- Measure cycles, storage, and replay/buffering cost.
- Demonstrate an FPGA build if it adds evidence.
- Only then run the gf180mcu flow and publish synthesis/physical evidence.

Exit: claims are limited to the actual completed flow stage.

## Milestone 5 — instrument

- Build generate/audition/repeat/save/vary workflow around the verified core.
- Add parameter locking and favorite recall.
- Evaluate whether nebula sampling belongs on host or device.
- Treat live keyboard semantics as a separately named, separately verified
  profile.
