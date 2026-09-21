# DR-0003: Resolve patches on the host and replay for normalization

- Status: Accepted (reviewed merge; 2026-09-21 — acceptance record below)
- Date: 2026-09-18
- Decision owners: 2AM Logic

## Accepted decision

The proposed decision below is accepted verbatim for the first exact-profile
core: host-side patch resolution; one execution lane; one render to find the
peak; deterministic replay at `1 / peak` when the peak exceeds one, at unity
otherwise; no complete-clip buffering to normalize; no limiter, AGC, or
constant-headroom substitute in the compatibility profile. No behavioral
relaxation is part of this acceptance.

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

## Acceptance record (2026-09-21)

Accepted by the reviewed merge of the issue #63 ratification PR, under the
operator's standing session delegation of 2026-09-20; the Builder does not
approve its own PR. Per-item basis against the measurement list above:

1. **Replay determinism for every stateful module.** The frozen fixed model
   (`src/torchsynth_voice/fixed_voice.py`, PR #156) renders as a pure
   function of the resolved request: phase accumulators inject their initial
   phase at sample 0 (`src/torchsynth_voice/fixed_voice.py:335-341`), the
   control path is a pure function of the physical map
   (`src/torchsynth_voice/format_sweep.py:277`), and the noise stream comes
   from request bytes; two byte-identical full regenerations were
   demonstrated at freeze. RTL replay determinism is the sample-exact
   fixed-model→RTL contract verified against
   `sim/reference/fixed-voice-golden-v1.json` (per-trace digests) by #78/#79.
2. **Cycle, SRAM, and energy cost of replay versus buffering.** Delivered by
   DR-0010's P4 cost analysis (issue #63, the deferral DR-0008 §15 records),
   computed from the frozen composition's counted operations at declared
   candidate clocks (25/50/100 MHz): two-pass replay costs 352,800 x C
   cycles per clip (86.4 M at C=245, +0.85 s per clip at 50 MHz against
   buffering) and ~zero clip SRAM, where buffering costs +176,400 x 24 b =
   516.8 KiB and saves one pass. Delivered in the estimate-class form
   #63's acceptance criteria admit (cycle budgets estimated; no PPA/fit
   claim from estimates); absolute energy measurement stays with #82 (P5).
3. **Exact noise stream requirements.** DR-0008 C8 (Accepted): host-fed
   exact binary32 stream, slot `sound_index % 32`, seed 13; noise-identity
   rows exact-bytes in the landed sweep receipt
   (`sim/candidates/audio-sources-sweep-v1.json`). The replay re-feed
   obligation (2 x 705,600 B per clip, digest-bound per pass) is recorded in
   DR-0010's clip lifecycle.
4. **Division/reciprocal precision and output arithmetic.** Measured by
   issue #52 / PR #150 (`sim/reference/normalization-reciprocal-v1.json`):
   reciprocal-multiply, F=22 fractional bits, gain word U1.22 (width 23),
   half-even at S5; worst case 524289/2^41 ≈ 2.3842e-7, within half an
   output LSB of the direct-division floor; ratified as register C9
   (DR-0008 §12/§15).
5. **UI/transport cost of a 78-parameter resolved patch.** The host-boundary
   decision is evidenced by the landed protocol v2 (#62, PR #155): the
   name-keyed load over the 78-name canonical inventory with hash-bound
   atomic commit, idempotent retry, and a transport-agnostic frame envelope
   (`spec/protocol/PATCH-LOAD.md:5-24`, `spec/protocol/SESSION.md`). The
   per-transport throughput figure remains an explicitly open
   transport-selection obligation (`spec/protocol/TRANSPORTS.md`, lane #66);
   it parameterizes deployment cost and settles nothing about this record's
   boundary, which is what this acceptance decides.

The replay architecture selected under this acceptance is the deterministic
two-pass re-render (DR-0010 P4); the unity branch replays the graph too, as
this record's decision text states. No synthesis, layout, signoff, hardware
playback, or sound-fidelity claim is made or enabled by this acceptance.
