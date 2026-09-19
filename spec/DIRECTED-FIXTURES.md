# Directed default Voice fixtures v1

[directed-voice-v1.json](reference/directed-voice-v1.json) preregisters 392
patches against the landed [parameter inventory](PARAMETER-INVENTORY.md).
[directed-coverage-v1.json](reference/directed-coverage-v1.json) connects every
coverage intent to concrete case IDs. This is a fixture preparation result:
all trace claims are `planned`, normalization peaks are `unmeasured`, and
`audio_render` is `not_run`. Static validation does not establish signal
activation, normalization branch coverage, or sound fidelity.

The target remains commit `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`, default
nebula, CPU float32, four seconds at 44,100 Hz / 441 Hz control rate. Each case
selects noise seed 13, stream slot 0 for later qualification. The 32-member
reference batch is a qualification protocol; the product executes one sound
per trigger. A scalar `batch_size=1, reproducible=False` experiment is allowed
but does not establish scalar/batched equivalence. This manifest has no random
corpus sound indices or holdout allocation.

## Generate, validate, consume

From the project root, with only Python's standard library:

```sh
python3 tools/generate_directed_fixtures.py
python3 tools/generate_directed_fixtures.py --check
python3 -m unittest discover -s tests -p test_directed.py -v
python3 tools/check_contract.py
```

`--manifest` and `--coverage` select output paths. `--check` compares both
files byte for byte and writes neither. The generator uses the inventory's
public `load_json` and `validate_inventory`, binds its exact file hash, and
never imports Torch. Duplicate JSON keys are rejected by that loader.

```python
from torchsynth_voice.directed import MANIFEST_PATH, resolve_patch, validate_manifest
from torchsynth_voice.inventory import load_json

manifest = load_json(MANIFEST_PATH)
validate_manifest(manifest)
case = next(c for c in manifest["cases"] if c["id"] == "source:noise")
patch = resolve_patch(manifest, case)  # all 78 canonical names -> value pairs
```

The base is a full name-keyed map; each case has explicit name-keyed
overrides, a target, variant, and purpose. Resolution copies pairs and never
uses array positions. Validation rejects missing/unknown names, duplicate or
missing cases, positional patches, non-finite/bool numbers, non-binary32
normalized inputs, incorrect physical conversions, invalid waveform weights,
unsupported intents, altered supporting contexts, and stale identities.
The [JSON Schema](schemas/directed-voice-v1.schema.json) is the structural
contract; `validate_manifest` supplies the cross-field semantic checks.
Coverage is regenerated only from a validated manifest; editing coverage text
alone fails `--check`.

Digest and content-version tokens must match in full, without leading or
trailing line terminators. Descriptive text must contain a character outside
Python's `str.strip()` whitespace set. Meaningful text may retain surrounding
whitespace and multiple lines; it is not trimmed or rewritten. The schema uses
strict end assertions and an explicit whitespace set so Python JSON Schema
and ECMAScript regex engines agree, including on Unicode line separators.

## Conversion truth

`normalized` is the authoritative, exactly representable binary32 input.
`physical` is an **analytic** value, not an observed Torch conversion. Policy
`pinned-equations-rational-v1` evaluates the pinned mapping equations as exact
rationals and rounds once to binary64. All default reciprocal curves are
integers (1, 2, 4, 5, 40), so rational evaluation avoids host transcendental
library variation. It preserves the supplied inventory bounds, including
`±3.1415927410125732`, not Python's `math.pi`.

For nonsymmetric ranges, the equation is `min + (max-min) * n**(1/curve)`.
For the default symmetric ranges, set `d = 2*n-1`, then use
`min + (max-min)/2 * (1 + sign(d)*abs(d)**(1/curve))`. Upstream's symmetric
`curve == 1` branch uses `n` in that final expression rather than `d`; the
helper preserves it, although no default Voice parameter uses that branch.
Design-time physical requests use the source's inverse equation, rounded to
binary32, and then recompute the recorded physical value from that input.
They do not silently retain an unattainable requested physical value.

Torch uses intermediate float32 operations and `log2`/`exp2`; those results
can differ from analytic pairs. A later adapter must set normalized parameters
directly by canonical name, freeze all 78, and record actual runtime physical
values separately. Passing these analytic physical values to `set_parameters`
would introduce another conversion round trip and is not this protocol.
There is no new audio tolerance, DSP implementation, numeric format, or
normalization policy here.

## Coverage and topology

| Family | Cases | Checked preparation |
| --- | ---: | --- |
| Parameter boundaries | 324 | Every name: 0, lower interior, upper interior, 1; four symmetric ranges also have center and both center neighbors |
| Matrix routes | 20 | One unit-gain input per destination column; competing inputs zero |
| Audio sources | 3 | Sine VCO, square/saw VCO, noise, each alone at the mixer with an amplitude route |
| Continuous waveforms | 15 | Each LFO's five individual weights and unequal five-way blend; VCO2 square, midpoint shape, saw |
| Envelope edges | 24 | Each of six ADSRs: zero attack/decay/release, note-off during attack, during decay, and a release extending past the clip |
| Silence/stress | 3 | Zero mixer, `2**-20` sine mixer gain, all routes/mixers and modulation/rate depths at maximum |
| Normalization targets | 3 | Exact binary32 peak neighbors below/at/above one, pending capture |

Interior normalized boundary points normally use `2**-12` and `1-2**-12`.
For `mixer.noise`, the lower interior point is `2**-3`, mapping to `2**-120`;
the generic point would underflow after the power-40 conversion in float32.
Symmetric center neighbors use `0.5 ± 2**-5` so the VCO fifth-power mapping
does not collapse into the center under float32 cancellation. These are
declared neighborhoods, not claims of adjacency in both numeric domains.
Zero route/depth/gain endpoints intentionally deactivate their contribution.

Every boundary has a routed context relevant to its parameter: main ADSRs
drive amplitude; LFOs and their envelopes drive pitch with a supporting
carrier amplitude route; VCO modulation depth has an active pitch input;
noise/mixer controls select the corresponding audio path. Supporting inputs
are checked as well as the varied value. All 78 controls are continuous;
there are no discrete selector enums to cover. LFO weights are raised to the
pinned exponent before normalization. Zero denominators, including obvious
float32 power underflow, are rejected even for an inaudible LFO. No fallback
waveform or weight normalization is applied by this tool.

The report enumerates 32 named capture intents covering the measurement plan's
keyboard, six envelopes, raw/gated LFOs, five matrix outputs, five upsamplers,
three raw/post-VCA sources, and mixer/peak/gain/output. These names describe
boundaries for the later trace adapter, not a claim that it already exists.
Pitch-route isolation needs a separate amplitude route to make the carrier
audible. Envelopes, raw oscillators, and mixer reduction always execute in
Voice; individual execution isolation is impossible without changing graph
topology. Each report entry states that limitation and its capture cases.

Normalization targets are `1-2**-24`, `1`, and `1+2**-23`. Candidates use a
flat-envelope sine path, with two amplitude routes for the above-one case.
Sampled peaks may miss these targets. A later render must capture the complete
pre-normalization clip, peak, and gain and demonstrate the **exact** intended
peak before claiming branch coverage. Strict `peak > 1` remains unchanged;
an exact tie retains input. No fitting, hidden scaling, or tolerance can
promote these pending claims. An unsuccessful candidate remains uncovered;
an amended patch needs a new preregistered content version before rendering.

## Identity and follow-on evidence

The schema version is 1. The manifest content version is
`directed-voice-v1-sha256:<digest>`. SHA-256 covers compact, sorted-key JSON of
the entire document except `identity`, with non-finite values forbidden.
Changing any patch, purpose, protocol, conversion policy, or inventory hash
changes both the digest and content version. Coverage binds that identity.
Regenerate after a deliberate recipe change; stale recorded identities fail
validation. This identity is local to this manifest and is not an audio
artifact `content_id`.

The next smoke render must apply every resolved patch to the pinned source,
record the actual runtime/lock and physical values, confirm finite output of
176,400 samples, and capture the intended traces. For reproducible batched
qualification, set/freeze the named patch across the batch, call forward with
a batch index, and select slot 0. Scalar experiments use forward without a
batch index. Never let forward randomization replace the directed patch.
Trace activation, exact normalization peaks, scalar equivalence, and audio
quality remain separate obligations in #21, #27, #28, #40, #41, #87, and #88,
as scoped by issue #17's wave-two guidance.

## Builder evidence — 2026-09-18

- Initial focused discovery failed because `torchsynth_voice.directed` did
  not exist; tests were written before implementation.
- Full discovery with `TORCHSYNTH_ROOT` pointing to the pinned checkout:
  91 tests passed, no skips, including 16 directed-fixture tests. Ordinary
  discovery without that variable: 86 passed, five existing source-backed
  inventory tests explicitly skipped. No normal test requires Torch.
- Deterministic generation/check, contract checks, Ruff lint and formatting,
  and independent Draft 2020-12 schema validation passed.
- An additional read-only upstream check verified the pinned source hashes
  and Git commit, then constructed CPU float32 default Voice with
  `batch_size=1, reproducible=False` under Python 3.13.2 / Torch 2.14.0.
  All 392 patches resolved to the actual 78 names: 30,576 normalized
  assignments read back exactly and all physical conversions were finite.
  Torch emitted the existing `pkg_resources` deprecation warning.
- That check performed **no audio render**. It establishes parameter
  assignment/conversion acceptance only; no canonical runtime qualification,
  scalar/batched equivalence, measured peak, or graph activation follows.
