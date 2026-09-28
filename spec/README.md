# Specification index

The specification describes what must be reproduced before implementation
details are allowed to become the de facto product.

- [`VOICE-CONTRACT.md`](VOICE-CONTRACT.md) is the current behavioral contract.
- [`FLOAT-VOICE.md`](FLOAT-VOICE.md) declares the composed independent float
  Voice and its end-to-end conformance record (#43).
- [`NORMALIZATION-REPLAY.md`](NORMALIZATION-REPLAY.md) records the fixed-path
  normalization reciprocal-precision measurement (#52): the DR-0003
  acceptance decision input, candidate-pending-ratification.
- [`RUBRIC.md`](RUBRIC.md) is the frozen verification rubric v0 (#48): the
  composed release decision procedure over every landed measurement family.
- [`CANONICAL-REFERENCE-QUALIFICATION.md`](CANONICAL-REFERENCE-QUALIFICATION.md)
  consolidates the canonical CPU reference phase (#3): how DR-0006/DR-0007
  discharge DR-0001's deferred runtime question, each acceptance criterion
  re-derived from the committed artifacts, and the divergences still open.
  `tools/check_reference_consolidation.py` re-runs the cross-check in CI.
- [`protocol/`](protocol/) specifies the core/host transport protocol subset
  that is independent of the pending numeric contract (#53).
- [`decision-records/`](decision-records/) records choices and unresolved
  boundaries.
- [`reference/upstream.json`](reference/upstream.json) pins upstream content.
- [`reference/corpus-v0.json`](reference/corpus-v0.json) preregisters the first
  random corpus without storing generated audio in Git.

An accepted decision record may change the contract. Code alone may not.

