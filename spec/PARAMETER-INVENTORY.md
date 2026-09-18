# Default Voice parameter inventory

The [inventory](reference/parameter-inventory-v1.json) describes all 78 named
parameters in `torchsynth-1-voice-default`, at commit
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`. It accounts for all 156 entries in
the pinned default nebula. It is descriptive and introduces no DSP behavior,
hardware formats, protocol IDs, or runtime qualification.

## Regeneration and checking

Use a separate checkout or archive of the pinned commit. Do not use a current
upstream working tree with different source hashes. From the project root:

```sh
python3 tools/check_contract.py --torchsynth-root /path/to/pinned-torchsynth
python3 tools/generate_parameter_inventory.py --torchsynth-root /path/to/pinned-torchsynth
python3 tools/generate_parameter_inventory.py --torchsynth-root /path/to/pinned-torchsynth --check
TORCHSYNTH_ROOT=/path/to/pinned-torchsynth python3 -m unittest discover -s tests -v
```

For a Git checkout, the checker also verifies HEAD; add `--require-git-commit`
to reject archives without Git metadata. Generation always verifies every
source hash in [upstream.json](reference/upstream.json) before parsing any
upstream source. The default nebula must contain exactly one `curve` and one
`symmetric` entry for each extracted name. Unknown, duplicate, or missing
entries fail. This nebula overrides curve and symmetry, with no min/max
overrides; the reported bounds are the effective source-declared bounds.

`--check` compares exact UTF-8 bytes and never writes the output. Generation
has no timestamps, host paths, network access, or Torch imports. The dedicated
[CI workflow](../.github/workflows/parameter-inventory.yml) fetches the selected
commit, verifies its hashes and HEAD, regenerates the inventory, and runs the
source-backed tests. CI therefore checks the actual upstream source rather
than only the internal consistency of committed JSON. Ordinary unit discovery
needs only the standard library; five source-backed tests explicitly skip if
`TORCHSYNTH_ROOT` is absent. The dedicated CI job supplies it.

## Facts and reviewed annotations

Generated facts and hand-authored interpretations have separate ownership:

- [parameter-annotations-v1.json](reference/parameter-annotations-v1.json)
  contains the reviewed units, meanings, continuous/selector interpretations,
  notes, and source-symbol evidence, keyed by exact canonical name.
- [parameter-inventory-v1.json](reference/parameter-inventory-v1.json)
  contains extracted facts and an `annotation` copy from that file for each
  row. Its provenance includes the annotation SHA-256, manifest SHA-256,
  upstream commit, and all six source/nebula hashes.
- [parameter-inventory-v1.schema.json](schemas/parameter-inventory-v1.schema.json)
  defines the inventory and the separate annotation document (`$defs.annotations`).
  The generator checks both schemas, exact annotation name coverage, unique
  names, finite numeric fields, increasing bounds, positive curves, boolean
  symmetry, and both order permutations. The standard-library validator
  implements only the schema vocabulary used here and rejects unknown schema
  keywords. A change to that vocabulary requires extending the validator.

Edit annotations in their source file and regenerate; do not edit their
copies in the generated inventory. No unknown unit or unspecified selector
interpretation is silently accepted. All default Voice parameters are
continuous. In particular:

- `keyboard.midi_f0` is a continuous MIDI pitch coordinate, without integer
  note rounding.
- `lfo_*.sin/tri/saw/rsaw/sqr` are continuous weights. Upstream raises the five
  values to its exponent and divides by their sum. All-zero weights have a
  zero denominator; this inventory supplies no fallback or repair.
- `vco_2.shape` continuously changes square to saw. `HardModeSelector` is not
  a component of this Voice.
- `alpha`, sustain levels, mix levels, and modulation-matrix gains are
  dimensionless, with their different meanings stated explicitly. LFO
  `mod_depth` supplies Hz per unit dimensionless envelope input; VCO
  `mod_depth` supplies semitones per unit pitch-control input.

The bounds describe `ModuleParameterRange` attributes, not a reimplementation
of its mapping equations or a claim about endpoint numerical behavior.
In particular, pinned `util.py` overwrites `torch.pi` with
`torch.acos(torch.zeros(1)).item() * 2`. Under the default float32 dtype this
is `3.1415927410125732`, so all four initial-phase ranges use that bound
rather than Python's double-precision `math.pi`. The extractor verifies that
source expression and rounds `acos(0)` to float32 before doubling it.

## Orders and freezing

Canonical names use `<module>.<parameter>`, including upstream `->` in
modulation routes. Rows are sorted by canonical name for display. Persist
patches by name; display order is not an interchange format.

Positions are zero-based and describe an unmodified default Voice:

| Field | Upstream traversal | First two names |
| --- | --- | --- |
| `forward_position` | `AbstractSynth.forward`: `self.parameters()` | `keyboard.midi_f0`, `keyboard.duration` |
| `randomization_position` | `AbstractSynth.randomize`: `sorted(self.named_parameters())` | `adsr_1.alpha`, `adsr_1.attack` |

The extractor reads Voice's `add_synth_modules` registration list and each
module's range declarations, including inherited VCO ranges and generated
LFO/mixer ranges. `SynthModule.add_parameters` inserts each range into
`torchparameters` individually, preserving that list order; the forward
traversal follows module registration order. Randomization instead sorts the
full `module.torchparameters.parameter` names, retained as `torch_name`.
These are independent mappings, not the order returned by
`get_parameters()` or the nebula JSON.

All 78 entries support upstream freezing and start unfrozen. `default_frozen`
is extracted from `ModuleParameter.__new__`; `frozen_capable` records the
pinned `set_parameters(freeze=True)`, `freeze_parameters`, and
`unfreeze_all_parameters` support. A frozen entry keeps its forward position
and its seeded randomization draw position even though assignment is skipped.
The inventory does not prescribe a device lock protocol.

The extractor deliberately supports the selected source's declaration forms,
not arbitrary Python or future TorchSynth versions. Source hashes reject
changes before those assumptions can silently follow upstream. These metadata
orders impose no hardware batching requirement: the product still renders one
four-second sound per trigger. Batched reference execution remains a fixture
qualification protocol.

## Builder verification evidence — 2026-09-18

The selected commit was exported separately and also fetched into a fresh Git
checkout. The checkout passed `check_contract.py --require-git-commit`,
including all six required file hashes and the checkout-only example hash.
The dedicated CI commands above ran locally against that checkout:

- `generate_parameter_inventory.py --check`: 78 parameters, 156 nebula
  entries, both permutations verified.
- Full unittest discovery with `TORCHSYNTH_ROOT`: 19 tests passed, no skips.
- Focused inventory discovery with `TORCHSYNTH_ROOT`: 13 tests passed,
  no skips, including two byte-identical regenerations and deliberate
  source/range/nebula/inventory corruption rejection.
- Ordinary discovery without the source environment variable: 14 passed,
  five explicitly skipped. `check_contract.py`, compileall, Ruff lint and
  format checks passed.

An additional real-upstream introspection used Python 3.13.2, Torch 2.14.0,
CPU, default float32, `Voice(SynthConfig(batch_size=1, reproducible=False),
nebula="default")`. It compared each `get_parameters(include_frozen=True)`
entry's minimum, maximum, curve, symmetry and frozen default with the
generated facts, mapped `voice.parameters()` and sorted
`voice.named_parameters()` back to canonical names by object identity, and
froze/unfroze all parameters. All 78 entries and both 78-position orders
matched exactly. This check initially exposed the float32 pi bound; the
regression test failed before its correction and passed afterwards.

This is metadata evidence only. No audio render, sound-fidelity verdict,
scalar/batched equivalence claim, or qualified runtime lock follows from it.
