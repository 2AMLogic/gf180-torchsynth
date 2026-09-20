# DR-0010: One-shot RTL microarchitecture skeleton (draft)

- Status: Proposed (draft skeleton; budget/normalization/schedule sections PENDING measurement — ratification gated on #52/#53/#54 closures)
- Date: 2026-09-20
- Decision owners: 2AM Logic
- Scope: interface coordination only — the skeleton that issue #63 will ratify
  into the one-shot RTL microarchitecture. It records which interface facts
  are already decidable from landed artifacts (datapath word formats, module
  inventory, sample grid, host binding shape, substrate governance) and names,
  per entry, every measurement-gated decision that is **not** made here:
  clocks/cycles-per-sample budget, memory macro versus distributed RAM, the
  time-multiplexing schedule, the normalization replay architecture, and all
  gf180 area/fit consequences. This record decides none of those; it is the
  ledger that keeps them from being decided silently. It does not change the
  numeric contract (DR-0008), the host boundary (DR-0003), substrate
  governance (DR-0005), or the one-shot profile (DR-0002). Landing tracked by
  issue #141; filed under the operator's standing session delegation
  ("do not stop until the goal is achieved", 2026-09-20), following the #138
  precedent for draft landings.

## Problem

#63 owns module interfaces, cycle/sample contracts, RAM/LUT layout,
time-multiplexing, pipeline latency, the normalization schedule and the host
protocol binding, and its dependencies (#52, #53, #54) are all open — so no
ratifiable microarchitecture decision exists yet. Meanwhile the measurement
chain (#50, #51, #52, then #53/#54) is running against artifacts that already
fix real interface facts: DR-0008's selected Choice Register, the landed
fixedpoint library, DR-0002's accepted profile grid, and #62's landed protocol
subset.

Undeclared, those facts stay scattered: each #63-lane PR either re-derives
them, or risks freezing an interface on a number whose status it misreads.
Conversely, the decisions that genuinely await measurement — budget, memory,
schedule, normalization replay — have no named ledger, so downstream work
cannot tell "selected but unratified" from "undecided", and #63's
cheap-refutable-gate rule ("the schedule must stop at the first failed premise
rather than continuing to produce downstream artifacts with no valid claim")
has nothing to stop against. This skeleton is that ledger: a DR-0008
§12-style choice register where every row is either grounded in a landed
artifact or explicitly `pending measurement` with its owning issue named.

## What is grounded (decidable now, from landed artifacts)

**Datapath sizing.** DR-0008's operator ruling of 2026-09-19 selected the
word formats; the machine-readable state is
`spec/reference/fixedpoint-choices-v1.json`, every entry carrying status
`selected (operator ruling 2026-09-19); pending ratification` and none
`accepted` while DR-0008 is Proposed
(`spec/decision-records/0008-fixed-point-numeric-contract.md:293-302`;
register table `:304-315`). The skeleton inherits exactly these, unchanged:

| Quantity | Selected format | Register status |
| --- | --- | --- |
| Audio sample word (all audio-rate and control-rate signals) | 24-bit Q2.21, range [-4, +4), LSB 2^-21 | selected (operator ruling 2026-09-19); pending ratification |
| Phase accumulator (per oscillator) | unsigned 32-bit, modular wrapping, 2^32 units/turn | selected (operator ruling 2026-09-19); pending ratification |
| Frequency word (control→VCO interface) | 32-bit Q16.15 | selected (operator ruling 2026-09-19); pending ratification |
| MIDI-domain control arithmetic | 32-bit Q10.21 | selected (operator ruling 2026-09-19); pending ratification |
| Multiplier/accumulate internals | 24x24 → 48-bit products; 48-bit accumulators in VCA/mixer lanes; no intermediate rounding before a declared narrowing site | selected (operator ruling 2026-09-19); pending ratification |

Peak evidence for the audio word remains the measured pre-normalization peak
3.9478583336 (`spec/decision-records/0006-canonical-runtime.md:89`); rounding
is half-even at sites S1–S5 and saturation carries sticky per-site counters,
with phase/frequency never-saturating and the Nyquist clamp forbidden (DR-0008
Sections 2 and 6). Consumers must refuse every one of these values wherever
the contract requires accepted ones — they are candidate instantiation data
only (`src/torchsynth_voice/fixedpoint/README.md:37-49`).

**Module inventory.** The landed fixedpoint library
(`src/torchsynth_voice/fixedpoint/README.md:13-35`) already names the
RTL-ownable primitives and their bit-exact software definitions: explicit
word formats as data (`formats`), one canonical integer rounding scalar
(`rounding`), sticky named per-site saturate/overflow counters (`counters`),
policy-driven saturate/wrap/rescale/mul/add/accumulator (`ops`), unsigned
modular wrapping phase accumulators with injected initial phase (`phase`),
generator-emitted hash-linked quarter-wave tables with integer linear
interpolation and quadrant reflection (`lut`), and the refusal gate for
not-yet-accepted register values (`choices`). The skeleton coordinates
interfaces around this inventory and decides nothing new about any module;
each future RTL module's owner is an Epic #2 issue (#68–#78), and no RTL may
be called conformant to DR-0008 while that record is Proposed
(`spec/decision-records/0008-fixed-point-numeric-contract.md:317-338`).

**One-shot profile grid.** Exactly 176,400 mono audio samples at 44,100 Hz
per trigger, 441 Hz control rate, one resolved sound per trigger (DR-0002,
Accepted: `spec/decision-records/0002-one-shot-product-profile.md:27-35`;
`spec/VOICE-CONTRACT.md:20-32`). The graph the grid must execute: monophonic
keyboard, two LFOs, six ADSR envelopes, one sine and one square/saw audio
oscillator, deterministic white noise, 4-by-5 modulation matrix, VCAs and
final mixer — no ladder filter (`spec/VOICE-CONTRACT.md:48-59`). Contract
behavior the RTL schedule must preserve: endpoint-aligned control
interpolation (not zero-order hold), envelopes receiving note duration in
advance, conditional whole-clip normalization, and the seed-13 noise slot
(`spec/VOICE-CONTRACT.md:68-77`).

**Host binding shape.** #62's landed protocol subset fixes the binding
*shape* while deferring wire widths: transport separation (transport-agnostic
interface contracts for UART/SPI/USB binding), name-keyed patch load through
a versioned canonical name table, version/capability/profile/numeric-contract
fields and patch hash carried as opaque byte strings, idempotent
repeat/retry, and explicit error/reset semantics — with sample-payload
packing widths and any fixed-point field encoding left as opaque width
placeholders until the numeric contract binds them. The one-lane, name-keyed,
host-noise boundary it binds to is DR-0003's
(`spec/decision-records/0003-host-boundary-and-normalization.md:10-15`).

**Substrate governance.** DR-0005 (Accepted) governs all substrate reuse:
pinned local copies with conformance gates, no submodules, `klt` for ASIC
flow work, and no import without provenance and a named maintainer
(`spec/decision-records/0005-pinned-substrate-reuse.md`). Any RTL package,
memory model or flow tool the microarchitecture adopts lands under that
record's gates, not this one.

## Choice register (machine-readable status)

Per the DR-0008 §12 discipline ("choices are data",
`spec/decision-records/0008-fixed-point-numeric-contract.md:293-302`), every
row is data with exactly one of three statuses:

- `selected-pending-ratification` — grounded in DR-0008's register; usable
  only as candidate instantiation data, never where the contract requires
  accepted values;
- `pending measurement` — **no decision exists**; the owning issue is named
  per row and no downstream artifact may be produced from a row whose
  premise has failed;
- `grounded (accepted upstream)` — an inherited, already-accepted profile
  fact, recorded for coordination convenience; this record changes it never.

| ID | Choice | Status | Evidence | Owner / change trigger |
| --- | --- | --- | --- | --- |
| A1 | Audio word 24-bit Q2.21 [-4, +4), all audio-rate and control-rate paths | selected-pending-ratification | `spec/reference/fixedpoint-choices-v1.json` (C1); `spec/decision-records/0006-canonical-runtime.md:89` | DR-0008 review; new peak evidence outside [-4, +4) |
| A2 | Phase accumulator u32 wrapping, 2^32 units/turn, initial phase injected at sample 0 | selected-pending-ratification | register C2; landed `fixedpoint/phase.py` | DR-0008 review; new phase-semantics evidence |
| A3 | Frequency word 32-bit Q16.15 at the control→VCO interface | selected-pending-ratification | register C3 | DR-0008 review; upstream clamp evidence change |
| A4 | MIDI-domain control arithmetic 32-bit Q10.21 | selected-pending-ratification | register C4 | DR-0008 review; clamp-mismatch fixture evidence |
| A5 | Sine: 4096x24 quarter-wave LUT + integer linear interp; 1K quadratic fallback pre-authorized | selected-pending-ratification | register C5; landed `fixedpoint/lut.py`; M2 sweep | DR-0008 review; M2 sweep or #82 memory evidence |
| A6 | Rounding half-even at every declared narrowing site (S1–S5) | selected-pending-ratification | register C6; landed `fixedpoint/rounding.py` | DR-0008 review; directed fixture requiring an exception |
| A7 | Saturation + sticky per-site counters; phase/frequency never saturate; Nyquist clamp forbidden | selected-pending-ratification | register C7; landed `fixedpoint/counters.py`, `fixedpoint/ops.py` | DR-0008 review; upstream behavior change at pin |
| A8 | 24x24 → 48-bit products; 48-bit accumulators in VCA/mixer lanes; no intermediate rounding pre-narrowing | selected-pending-ratification | DR-0008 Section 2 table | DR-0008 review; new accumulator-growth evidence |
| A9 | One-shot grid: 176,400 samples @ 44,100 Hz, 441 Hz control, mono, one resolved sound per trigger | grounded (accepted upstream) | DR-0002 (`spec/decision-records/0002-one-shot-product-profile.md:27-35`); `spec/VOICE-CONTRACT.md:20-32` | profile change requires its own DR (per DR-0002) |
| P1 | Clocks and cycles-per-sample budget | pending measurement | DR-0008 M6 defers the per-sample budget to #63 (`spec/decision-records/0008-fixed-point-numeric-contract.md:262-264`); calibration needs the #50/#51 sweeps | **#63**; amend on sweep closure |
| P2 | Memory architecture: macro SRAM vs distributed RAM for the sine LUT, replay buffer and per-module storage | pending measurement | #82 measures gf180mcu feasibility; DR-0008 C5 change trigger names #82 memory evidence | **#82**; amend on measurement |
| P3 | Time-multiplexing schedule across 2 VCOs + 6 ADSR envelopes (+2 LFOs, mod-matrix lanes) | pending measurement | #50/#51 sweeps bound resource arithmetic; #63's cheap-refutable gate requires resource/bandwidth arithmetic before place-and-route | **#63** (decision), **#50/#51** (evidence) |
| P4 | Normalization replay architecture: two-pass re-render vs render-to-buffer-then-scale vs recompute-and-apply | pending measurement | #52 owns the measured decision; DR-0003 requires replay-vs-buffering cycle/SRAM/energy cost before acceptance (`spec/decision-records/0003-host-boundary-and-normalization.md:38-44`) | **#52**; candidates listed, **none selected here** |
| P5 | gf180 area/fit/PPA consequences of any datapath, memory or schedule row | pending measurement | #82 (synthesis/timing) and #83 (floorplan/place/route) | **#82/#83**; no PPA or fit claim from estimates (#63 AC) |

P4 note: the three candidate shapes are named as #52's reference set only.
This record selects nothing among them, pre-orders no preference, and takes
no position on buffer sizing; the buffer arithmetic (e.g. 176,400 x 24-bit
≈ 517 KiB) is #52's trade-study input, not a design fact.

## Change control

Which edits require what (DR-0008 §13 pattern,
`spec/decision-records/0008-fixed-point-numeric-contract.md:317-338`):

- Any change to an A-row's format or semantics: the underlying DR-0008
  change path first (a selected register value never moves here before it
  moves there), then an amendment to this record.
- Any P-row closure: an amendment citing the owning issue's measured
  evidence (sweep result, synthesis report, or #52's normalization
  decision), converting the row to a decided status. A P-row may never be
  closed by prose, estimate, or analogy.
- House rule, adopted verbatim from #63's cheap-refutable gates: the
  schedule must stop at the first failed premise rather than continuing to
  produce downstream artifacts with no valid claim. If #50, #51, #52, #53 or
  #54 closes with a result that invalidates a grounded A-row's premise, this
  skeleton is amended or withdrawn before further #63-lane work — never
  worked around.
- This record pre-commits nothing: no RTL, no testbench, no synthesis run,
  no committed byte, and no workflow is authorized by it.

## Consequences

- #63-lane work gains one citable interface surface: word formats, module
  inventory, sample grid and host binding shape with a single status
  vocabulary, so PRs stop re-deriving (or mis-freezing) these facts.
- The measurement-gated decisions are explicit, ownable and enumerable
  (P1–P5); "silently decided" is no longer a reachable state for them.
- Nothing is loosened: every grounded value is weaker-armed than its source
  (selected-pending-ratification, never accepted), and every pending row is
  stronger-armed than prose (owning issue named).
- Ratification of this record cannot precede the closures it waits for: the
  budget, normalization and schedule rows cannot be ratified as
  anything-but-pending while #52, #53 and #54 are open, so a reviewed merge
  before those closures ratifies only the skeleton's coordination content.
- No gf180mcu synthesis, layout, signoff, hardware playback, or
  sound-fidelity claim is made or enabled by this record; P5 exists to keep
  such claims out until #82/#83 produce committed evidence.

## Alternatives considered

- **Wait for #52/#53/#54 before filing anything.** Rejected: it forfeits the
  interface-coordination value for the whole measurement window and invites
  exactly the scattered-freeze behavior the ledger prevents; a draft with
  honest PENDING rows loses nothing the closures would add.
- **File a full microarchitecture DR now** (with budget, schedule and
  memory selections). Rejected: it would decide measurement-gated questions
  without evidence, violating DR-0004's no-omnibus/no-invented-verdict
  discipline and #63's stop-at-first-failed-premise rule.
- **Keep the skeleton in #63's issue body only.** Rejected: issue bodies are
  not versioned spec; the register form makes the statuses machine-checkable
  and amendment-controlled, matching the DR-0008 §12 precedent.

## Ratification

The draft is submitted for Loom review together with tracking issue #141.
Reviewed merge ratifies only what this record grounds; the P1–P5 rows stay
`pending measurement` until their owning issues close and this record is
amended. The Builder does not approve or merge its own PR. Until ratification
the skeleton is proposed: consumers must not instantiate RTL, testbenches or
flow runs against it, and must not describe any A-row as accepted or any
P-row as decided. Ratification of the budget/normalization/schedule content
additionally requires the #52/#53/#54 closures named in the status line.

## Citation basis

Repository citations use `path:line` form verified against `origin/main`
(head `b1079e0`, working tree identical, 2026-09-20). Upstream TorchSynth
claims are cited through this repository's records (DR-0002, DR-0003,
DR-0008, `spec/VOICE-CONTRACT.md`) and are not re-derived here. This record
makes **no** gf180mcu synthesis, layout, signoff, hardware playback, or
sound-fidelity claim, and no claim that any listed module, format or
schedule is implementable at any stated cost: area/fit evidence belongs to
#82/#83, cycle budgets to #63, and the normalization architecture to #52.
