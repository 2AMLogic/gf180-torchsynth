# DR-0003: Resolve patches on the host and replay for normalization

- Status: Proposed
- Date: 2026-09-18
- Decision owners: 2AM Logic

## Proposed decision

For the first exact-profile core:

1. The core has one execution lane and renders one sound per trigger.
2. The host performs nebula sampling and sends a resolved, name-keyed
   78-parameter patch.
3. The host or testbench supplies the selected canonical TorchSynth noise
   stream (or a
   later qualified generator supplies exactly the same stream).
4. The core renders the deterministic graph once to find the peak.
5. If the peak exceeds one, the core deterministically replays the graph and
   applies `1 / peak`; otherwise it replays at unity gain.

Do not buffer the complete clip merely to implement normalization. Do not
replace conditional whole-clip normalization with a limiter, AGC, or constant
headroom in the compatibility profile.

## Why proposed

The approach appears semantically exact and trades memory for a second render,
but its cost is not yet measured. It also places patch generation outside the
chip, which is appropriate for the first verification target but may not be the
desired final device boundary.

The single-lane decision does not make TorchSynth's `reproducible=False`
randomizer a new identity source. Canonical corpus identities still come from
the pinned synth1B1 generation contract; only their resolved patch and chosen
noise samples cross this hardware boundary. Issue #88 measures batch-shape
floating-point drift before the scalar float/fixed bridge is ratified.

Before acceptance, measure:

- replay determinism for every stateful module;
- cycle, SRAM, and energy cost of replay versus buffering;
- exact noise stream requirements;
- division/reciprocal precision and output arithmetic;
- the UI/transport cost of a 78-parameter resolved patch.
