# Specification index

The specification describes what must be reproduced before implementation
details are allowed to become the de facto product.

- [`VOICE-CONTRACT.md`](VOICE-CONTRACT.md) is the current behavioral contract.
- [`FLOAT-VOICE.md`](FLOAT-VOICE.md) declares the composed independent float
  Voice and its end-to-end conformance record (#43).
- [`RUBRIC.md`](RUBRIC.md) is the frozen verification rubric v0 (#48): the
  composed release decision procedure over every landed measurement family.
- [`protocol/`](protocol/) specifies the core/host transport protocol subset
  that is independent of the pending numeric contract (#53).
- [`decision-records/`](decision-records/) records choices and unresolved
  boundaries.
- [`reference/upstream.json`](reference/upstream.json) pins upstream content.
- [`reference/corpus-v0.json`](reference/corpus-v0.json) preregisters the first
  random corpus without storing generated audio in Git.

An accepted decision record may change the contract. Code alone may not.

