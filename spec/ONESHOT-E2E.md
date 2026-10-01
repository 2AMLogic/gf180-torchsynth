# One-shot RTL end-to-end conformance (issue #79)

Status: **the integrated whole-voice one-shot top now exists** and is proven
bit-identical to the frozen fixed model over its declared regression profile.
A **committed, commit-exact** evidence record for the whole-voice lane's
regression profile now exists (`sim/evidence/oneshot-whole-voice-regression-v1.json`,
citing commit `97ee7dd94d068bd7341f5ee02247e63f99597336`, which predates and
is unaffected by the current pull request -- see "Evidence identity"). Issue
#79 nonetheless stays open on: the tail-chain lane's own committed record (now
mechanically unblocked, not yet generated -- see below), and the `full`
profile run for both lanes that a release-era record must cite. See "What is
still open" below. Nothing here releases the "must pass before FPGA/gf180 fit
claims" gate.

This record covers two lanes, landed in that order:

| Lane | Top | Flow | Harness checks | What it composes |
| --- | --- | --- | --- | --- |
| tail chain | `tb/sv/one_shot_tail_top.sv` | `tb/run_oneshot.py` | `tests/test_oneshot_tail_chain.py` | `audio_mix_engine` (#76) -> `normalization_replay_engine` (#77) over host-fed source/amplitude streams |
| **whole voice** | `tb/sv/one_shot_voice_top.sv` | `tb/run_voice.py` | `tests/test_oneshot_voice.py` | **every landed Epic #2 engine**: #70 x6, #71 x2, #72 (matrix + 5 upsample columns), #73, #74, #75, #76, #77 |

The tail chain is retained rather than superseded: it is the cheap lane that
isolates the normalization seam, and the whole-voice lane's own
`mixer.pre_normalization` / `link.replay_input` traces are the same seam under
a far larger stimulus. Both lanes change no arithmetic profile, noise policy,
normalization, parameter ordering or clip timing, and neither adds
decision-record-gated behavior.

Neither lane makes any synthesis, layout, signoff, hardware-playback or
sound-fidelity claim, and no float tolerance exists in either flow
(`float_tolerance` is recorded as `null`, and both comparators are exact
integer equality end to end).

---

## 1. The whole-voice lane (`tb/run_voice.py`)

### What is composed

```
6x adsr_engine (#70) --+
                        +--> 2x lfo_vca_engine (#71) --+
                        |                              |
                        +------------------------------+--> mod_matrix_engine (#72)
                                                               |
                                      5x upsample_engine (#72) <+ (column load)
                                               |
           +-----------------------------------+------------------+
           |                 |                                    |
  sine_vco_engine (#73)  square_saw_vco_engine (#74)    (amp columns)
           |                 |        noise_stream_dut (#75)      |
           +-----------------+----------------+-------------------+
                                              |
                                  audio_mix_engine (#76)
                                              |  (the link seam)
                                normalization_replay_engine (#77)
```

A "case" is one control walk (1,764 four-cycle ticks) plus **two** complete
176,400-sample audio passes, plus the 176,400-sample released output clip.
Pass 2 is a full re-render of the audio chain over the retained control
columns and a re-streamed noise clip -- DR-0010 P4's two-pass replay with no
clip buffer anywhere in the top. The five upsample column memories hold 1,764
control words per route, not 176,400 samples, and are the only
control-to-audio crossing in the profile.

### What the host still supplies, and why

- **The declared shadow words.** DR-0010's 2026-09-22 amendment (issue #74
  remainder, tracking #172) ratifies the transcendental sub-expressions --
  `exp2` on both pitch paths, `partials_constant`, `tanh`, the ADSR `**alpha`
  powers and the LFO shape weights -- as host-replayed deterministic words at
  the declared shadow boundary. They cross into the top exactly as each
  engine's own declared interface takes them. That is the ratified
  architecture, not a gap in this composition. The flow asserts every shadow
  mirror equal to the model's corresponding trace before simulating, so a
  drift in any mirror **refuses the run** rather than silently weakening the
  comparison.
- **The S1 entry words** (quantized patch parameters) and the exact C8 noise
  byte stream (705,600 bytes per pass): DR-0003's host boundary.
- **Phase sequencing** (`ctl_*` / `audio_*` enables, `start`, `mix_done`) and
  the two host-fed checkpoints `keyboard.midi_f0` / `keyboard.duration`.

### What this top does NOT contain

- The serialized single-MAC schedule (DR-0010 P1/P3 schedule-candidate):
  every engine still retires one sample/tick per *enabled* cycle. The audio
  group is four cycles wide only because the noise lane's byte port is 8 bits.
- The #69 protocol receiver (see `render_binding_top.sv`, a separate declared
  composition).

### Cases

- **receipt cases**: the 26 param-committed cases of
  `sim/reference/fixed-voice-golden-v1.json`. Their `mixer.output`,
  `mixer.gain`, `mixer.peak` digests and fixed branch decision are pinned to
  the frozen receipt before the RTL is compared.
- **directed fixtures**: `special:silence`, `special:near-silence`,
  `special:stress` (`spec/reference/directed-voice-v1.json`).
- **derived fixture** `voice:divide-distinct-levels`: `special:stress` with
  `mixer.vco_2=0.75`, `mixer.noise=0.5` (three distinct level words, divide
  branch). Declared because the receipt's own divide-branch cases
  (`global-0/2/52/63`) are digest-custody corpus items whose physical
  parameters are never committed, so no stimulus can be built from them.
- **derived fixture** `voice:binding-distinct`: `special:stress` with every
  stimulus symmetry broken — see "The binding case" below.

The regression profile is `normalization:below` (bypass branch,
param-committed) plus `voice:divide-distinct-levels` (divide branch): the
smallest set that covers both normalization branches at full clip length.
`voice:binding-distinct` is additionally derived and run pristine over the
declared prefix cap in `regression`, and is a committed full-length case in
`full`.

### The binding case (and why a uniform stimulus is not enough)

`special:stress` is deliberately uniform: **all twenty modulation depths are
1.0, all six envelope parameter sets are identical, and the two LFO sides are
identical.** Under that stimulus a whole class of genuine RTL binding faults
produces *bit-identical* output — permuting the matrix source columns, swapping
the LFO rate/gain envelope roles, or swapping two amplitude or pitch routes all
exchange equal words. This is not a hypothetical: **the first run of this lane
reported four controls as NOT DETECTED for exactly this reason.** A control
that cannot fail is not a control.

`voice:binding-distinct` breaks every such symmetry:

- twenty **mutually distinct** modulation depths, laid out so each *route* row
  occupies its own band and the two pitch rows sit at opposite ends of the
  range (`vco_1_pitch` 0.07–0.17 against `vco_2_pitch` 0.89–0.97), so a pitch
  swap is not a sub-quantum perturbation;
- six **distinct** envelope formations, each with an attack shorter than the
  prefix-capped walk (6,000 audio samples = 60 control ticks = 0.136 s), and
  with each side's rate envelope differing from its gain envelope;
- two **distinct** LFO sides (rate, depth, initial phase and shape mix);
- three distinct mixer level words.

`tests/test_oneshot_voice.py` asserts each of those properties — and asserts
that the base fixture really is degenerate — so a future edit cannot quietly
restore a uniform stimulus and turn six controls back into no-ops. The pristine
RTL is proven bit-exact on this case over the prefix cap **before** any mutant
is planted, so a DETECTED verdict is attributable to the mutation and not to a
pre-existing divergence.

### Compared traces (27 named traces + the output clip)

Control-rate (1,764 ticks each): the six envelope outputs
(`adsr_1`, `adsr_2`, `lfo_{1,2}_rate_adsr`, `lfo_{1,2}_amp_adsr`),
`lfo_{1,2}.raw`, `lfo_{1,2}.post_control_vca`, and the five
`mod_matrix.*` route words.

Audio-rate (176,400 samples **per pass**): the five `control_upsample.*`
columns, `vco_1.raw`, `vco_2.raw`, `noise.raw`, the three `*.post_vca`
columns, `mixer.pre_normalization`, and -- separately -- the replay
controller's own consumed stream `link.replay_input`, sampled on the
`link_valid` seam.

Plus `mixer.output`, `mixer.peak`, `mixer.gain`, every status/error register,
the controller's own `pass_index` on each captured sample, and the op counters
of all sixteen engine instances.

`tests/test_oneshot_voice.py` asserts that this compared set is **exactly**
`float_voice.voice_checkpoints()` minus the two host-fed keyboard
checkpoints, so no declared checkpoint can be silently left uncompared.

### Negative controls -- every one is a genuine RTL mutation

This is the substantive difference from the tail-chain lane, where four of ten
controls were stimulus-only: the RTL that owns the parameter-shuffle,
wrong-noise, interpolation and gain/route faults is now composed in, so each
fault is planted **in RTL**.

| Control | Mutated source | Case | Planted fault |
| --- | --- | --- | --- |
| `matrix-column-shuffle` | `one_shot_voice_top.sv` | binding | the matrix's four source columns permuted against the pinned (adsr_1, adsr_2, lfo_1, lfo_2) order the twenty depth words are packed in |
| `lfo-envelope-swap` | `one_shot_voice_top.sv` | binding | the LFO rate-envelope and control-VCA gain-envelope roles swapped on both sides |
| `amp-route-swap` | `one_shot_voice_top.sv` | binding | the vco_1 VCA driven by the noise amplitude column |
| `pitch-column-load-swap` | `one_shot_voice_top.sv` | binding | the two pitch route words land in each other's upsample column memory at the control/audio crossing |
| `vco-pitch-wire-swap` | `one_shot_voice_top.sv` | binding | vco_1's pitch *wire* driven by the vco_2 pitch column — caught only on an op counter, see below |
| `wrong-noise` | `one_shot_voice_top.sv` | binding | the mixer consumes the *previous* noise sample (one-sample lag on the exact C8 stream) |
| `missing-sample` | `one_shot_voice_top.sv` | divide | one mixer sample (index 1000) dropped at the link seam; **must** localize to `link.replay_input[pass1]` sample 1000 or the run fails |
| `interpolation-zoh` | `upsample_engine.sv` | divide | zero-order hold instead of the endpoint-aligned blend |
| `mixer-truncate` | `audio_mix_engine.sv` | divide | truncation instead of half-even at the mixer narrowing |
| `normalization-always-off` | `normalization_replay_engine.sv` | divide | never divides (on a divide case) |
| `normalization-always-on` | `normalization_replay_engine.sv` | bypass | always divides (on a bypass case) |
| `normalization-wrong-reciprocal` | `normalization_replay_engine.sv` | divide | U1.22 gain word +1 ULP |
| `normalization-wrong-peak` | `normalization_replay_engine.sv` | divide | peak tracker keeps the last sample, not the max |

**The pitch wire is observable only on an op counter, and that is a property of
the ratified architecture.** `vco-pitch-wire-swap` changes **no** compared
sample trace. The frequency both VCOs integrate is the host-replayed `exp2`
shadow word (DR-0010's 2026-09-22 amendment), so inside the engine `up_pitch`'s
only consumer is the C4 MIDI sum, which is not an output of the #73 engine. The
control is still killed — by that engine's own declared op-count conformance
surface, where the wrong column changes the measured MIDI-clamp count
(`ops.V1.*[5]`: 0 against 5,683 on the binding case's first 6,000 samples). For
this to be a real gate, a prefix-capped walk must check those counters, so
`voice_derive_case` computes **prefix-exact** saturation and clamp tallies for
each declared cap by re-walking the model's own mirrors over the first *cap*
pitch words (exact, not an estimate: both mirrors are strictly sequential
per-sample walks). Closing the *trace*-level gap would require the #73 engine to
export its MIDI sum — an RTL change to a qualified module, so out of this
verification issue's scope and recorded under "What is still open".

`pitch-column-load-swap` covers the same route ordering one stage earlier,
where it *is* directly observable on `control_upsample.vco_1_pitch`.

Each must be DETECTED; an undetected control fails the run. Every anchor is
asserted to occur **exactly once** in the source it names
(`tests/test_oneshot_voice.py`), so a seam that drifts refuses loudly instead
of silently becoming an undetectable control, and every mutant is asserted to
still elaborate.

**Mutation walk lengths are declared, not incidental.** The four
normalization controls are demonstrated on a **full-length** walk, because the
replay controller's branch decision is only reached at sample 176,400. The
other nine are demonstrated on the first 6,000 audio samples
(`VOICE_MUTATION_WALK_CAP`); each demonstrably bites inside that prefix, and
the control walk is never capped. The declared walk **and case** of every
control are recorded per-control in the evidence record, so a reader never has
to assume which length or which stimulus a given verdict came from.

### First-mismatch localization

`voice_rows()` scans each named trace independently, reports that trace's
first differing position as `(trace, cycle, sample, expected, actual)`, and
then orders the sequence rows by the capture's own cycle number -- so `rows[0]`
is the earliest observable divergence in simulated time, not merely the first
trace in a list. An `x`-state emission is a mismatch, not a skip. A failed
committed case copies its whole raw artifact directory to a printed
`voice-failed-*` temp dir; `--workdir` retains everything unconditionally.

### Determinism and state independence

- **Two clean simulations** of `voice:divide-distinct-levels` at full length
  must be artifact-hash identical over all six artifact families
  (`ctlcap`, `audiocap`, `linkcap`, `outcap`, `status`, `ops`).
- **Back-to-back renders with no reset in between** (prefix-capped at 6,000
  samples -- state-leak evidence, not a length claim) must reproduce the solo
  run exactly and both must match the model: no phase accumulator, envelope
  index, column memory or peak tracker may leak across clips.

### Runtime / regression partition and evidence commands

| Profile | Cases | Command | Role |
| --- | --- | --- | --- |
| `regression` (default) | `normalization:below` + `voice:divide-distinct-levels` at full length, the `voice:binding-distinct` prefix baseline, the two hash simulations, the replay pair/solo and the thirteen mutation simulations | `python3 tb/run_voice.py --profile regression --workdir <dir>` | PR/CI gate (`tb-sim.yml` job `oneshot-whole-voice`) |
| `full` | all 26 receipt cases + 3 directed fixtures + both derived fixtures, same baseline/pair/hash/mutation simulations | `python3 tb/run_voice.py --profile full --workdir <dir>` | release-era evidence; remote AWS box per `CLAUDE.md` |

Both profiles walk the complete clip twice for every committed case; neither
caps a committed walk. Simulator-free harness checks:
`python3 -m unittest tests.test_oneshot_voice`.

Measured wall-clock for the whole `regression` profile:

| Host | Simulator | Total |
| --- | --- | --- |
| GitHub `ubuntu-24.04` (`oneshot-whole-voice` job, run 36894315125) | Icarus 12.0 (apt) | **33 min 32 s** |
| dev Mac, Apple silicon, serial | Icarus 13.0 | **54 min** |

Per full-length case on the dev Mac: ~30 s of model derivation, 0.7 s of
stimulus writing, **~10 min of Icarus simulation** (1.42 M simulated cycles;
~23 min if a second Icarus simulation shares the machine) and ~10 s of exact
comparison. The profile runs **eight** full-length simulations (2 committed
cases, 2 clean hash simulations, 4 normalization mutants) plus about twelve
prefix-capped ones (the binding baseline, nine capped mutants, the replay pair
and its solo). One full-length case retains ~35 MB of raw artifacts under
`--workdir` (`audiocap` alone is ~25 MB). The CI step budget is ~3.5x the
slower of the two measurements. These are indicative figures, not bounds. The
`full` profile is roughly fifteen times the committed-case work and is an
AWS-box command, not a PR gate.

**Pass a work directory the simulator can reach.** Every simulator invocation
runs with `cwd=<workdir>`, because the bench opens its stimulus and capture
files by bare name; the flow therefore resolves `--workdir` to an absolute path
before building any tool command. A relative path is fine to *pass* (the CI
lane passes `out/whole-voice` so the record can be uploaded as an artifact) —
it is simply never handed to a tool unresolved.

Exit status is the lane's own pass/fail; **exit 3 means Icarus Verilog is
absent and nothing ran** -- never reported as a pass.

### Evidence identity

`voice-evidence.json` (schema
`gf180-torchsynth/oneshot-whole-voice-evidence-v1`) is written on every run and
uploaded by the CI lane as the `oneshot-whole-voice-evidence` artifact. It
records `git_head`,
`git_tree_dirty` (whether `tb/`, `src/` or `spec/reference/` carried
uncommitted changes), the `iverilog` version banner, and sha256 of every RTL
and testbench source, the constants package,
`fixed-voice-golden-v1.json` and `directed-voice-v1.json`, plus every
mutation verdict with its declared walk length and `float_tolerance: null`.

**A record produced from a dirty tree, or whose `git_head` is not the commit
under review, is not citeable evidence.** Note that on a `pull_request` event
`actions/checkout@v4` checks out the PR's *merge* commit, so a CI-produced
record's `git_head` names that merge commit rather than the branch head — still
exact, but a different object than a local run on the same branch.

**A committed record now exists**:
`sim/evidence/oneshot-whole-voice-regression-v1.json`. It was generated on
commit `97ee7dd94d068bd7341f5ee02247e63f99597336` -- the clean tip of `main`
this pull request branched from, not a commit this pull request itself
introduces, which is the structural requirement explained below -- before any
file in this increment's own diff was written, so `identity.git_tree_dirty`
is `false` and `identity.git_head` names a commit this PR neither creates nor
modifies. Verified committable by
`python3 tools/verify_oneshot_evidence.py sim/evidence/oneshot-whole-voice-regression-v1.json --expect-head 97ee7dd94d068bd7341f5ee02247e63f99597336`
(exit 0) before being added here. It reports `result: PASS`, both branches
covered, and 13/13 mutations `DETECTED` -- the same regression-profile result
narrated in PR #253, now pinned as a committed artifact rather than only a CI
upload.

**Why this was possible even though a record cannot cite its own commit**:
the record's `git_head` names the commit the *flow ran on*, not the commit
*the record file itself lands in*. Those are different commits whenever the
flow is run before adding anything to the tree -- which is exactly what
happened here. What is still structurally impossible is citing a commit that
does not yet exist (this repository squash-merges, so a PR branch's own
intermediate commits never reach `main` -- only the squash commit GitHub
creates at merge time does, and no one can know that SHA in advance). The
tail-chain lane below remains open for exactly that reason: its flow had a
defect that made it impossible to generate a clean record from before this
increment, and the fix is itself new, uncommitted work in this diff, so this
PR cannot (and does not try to) produce a citeable tail-chain record. The
`full`-profile record for either lane has the same requirement again, every
time: it must be generated on an already-existing, clean, already-landed
commit, never predicted or faked from inside a PR.

---

## 2. The tail-chain lane (`tb/run_oneshot.py`)

The earlier, narrower lane: `audio_mix_engine` (#76) -> `normalization_replay_engine`
(#77), with the audio-rate sources and endpoint-aligned amplitude columns
**host-fed from the frozen model's own rows**. The mixer's registered
`mix_word`/`out_valid` pair drives the replay engine's `mix_in`/`mix_valid`
directly (the `link_valid` wire, a named mutation seam).

Its cases are the 26 param-committed receipt cases, the three directed
fixtures and the derived fixture `oneshot:divide-distinct-levels`; its
regression profile is `normalization:above/below/tie`,
`normalization-stress:anchor-3.9478583336`, `source:noise`, `special:silence`,
`special:stress` and `oneshot:divide-distinct-levels`.

| Profile | Command | Role |
| --- | --- | --- |
| `regression` (default) | `python3 tb/run_oneshot.py --profile regression` | PR/CI gate (`tb-sim.yml` job `oneshot-tail-chain`) |
| `full` | `python3 tb/run_oneshot.py --profile full --workdir <dir>` | release-era evidence; remote AWS box |

Simulator-free harness checks:
`python3 -m unittest tests.test_oneshot_tail_chain`.

**Its mutation caveat, retained verbatim because it still describes that
lane:** four of its ten controls (`parameter-shuffle`, `wrong-noise`,
`interpolation-zoh`, `gain-one-ulp`) are **stimulus-only** -- the pristine RTL
is fed a wrong level order / a rotated noise stream / a zero-order-hold
amplitude column / a +1-ULP noise level word, because the RTL owning those
faults is not composed in that top. The other six (four normalization
mutations, `missing-sample`, `mixer-truncate`) are genuine RTL mutations, and
`normalization-wrong-reciprocal` is its only genuine RTL *gain* fault. **The
whole-voice lane above closes exactly this gap**: all thirteen of its controls
are genuine RTL mutations.

Back-to-back replay note (both lanes): the replay engine's op counters are
free-running until reset, so the tail-chain replay check requires the second
run's counters to equal the solo run's plus the first run's; sample streams
and all non-counter status must match the solo run exactly.

### A discovered defect: a relative `--workdir` broke every tail-chain simulator invocation

PR #253 fixed this exact defect class in `tb/run_voice.py`'s `voice_simulate`
("A relative `--workdir` broke every simulator invocation") but did not
backport it to `tb/run_oneshot.py`, because the tail-chain's own CI job
(`tb-sim.yml`'s `oneshot-tail-chain`) calls `run_oneshot.py` with no
`--workdir` at all -- it uses a `tempfile.TemporaryDirectory`, whose path is
always absolute, so the defect never fired there. It fires on exactly the
invocation this spec itself documents as the release-era command
(`--workdir <dir>`) whenever `<dir>` is relative: every simulator invocation
runs with `cwd=workdir` (the bench opens its files by bare name), so
`iverilog -o out/tail-chain/case-.../oneshot.vvp` resolved against the
already-entered `out/tail-chain`, looked for
`out/tail-chain/out/tail-chain/case-.../oneshot.vvp`, and failed with "No such
file or directory" -- discovered by running exactly the evidence-generating
command this increment needed, `python3 tb/run_oneshot.py --profile
regression --workdir out/tail-chain` (a relative path), which crashed on the
first case before producing any record. Fixed the same way PR #253 fixed it:
`oneshot_simulate` now resolves its `workdir` to an absolute path before
building any tool command, and `main` resolves `--workdir` the same way for
every other caller. Re-running the identical command after the fix completes
the full regression profile and reports `ONESHOT RUN PASSED`.

This fix is itself new, uncommitted work in this increment's diff, so no
tail-chain evidence record generated on a tree containing it is a clean
citation of an already-landed commit (`identity.git_tree_dirty` is correctly
`true` on every run this increment made). The record becomes generatable --
not generated by this PR -- on the next already-landed, clean commit that
contains the fix (this PR's own merge commit, at the earliest):

```
python3 tb/run_oneshot.py --profile regression --workdir <absolute-or-relative-dir>
python3 tools/verify_oneshot_evidence.py <dir>/oneshot-evidence.json --expect-head <that commit>
# only if the verifier exits 0:
cp <dir>/oneshot-evidence.json sim/evidence/oneshot-tail-chain-regression-v1.json
```

---

## 3. Acceptance-criteria ledger (issue #79)

| # | Criterion | Whole-voice lane | Tail-chain lane |
| --- | --- | --- | --- |
| 1 | All required cases produce exactly 176,400 bit-exact samples and exact status/trace sequences | **Met for the declared `regression` profile** (both cases, 1,764 control ticks + 2 x 176,400 samples over 27 named traces + the output clip + every status/op counter). **Not yet run at `full`.** | Met for the tail chain only |
| 2 | First mismatch localized by trace/cycle/sample, raw artifacts retained | **Met.** `voice_rows` / `voice_print_rows`; rows ordered by cycle; `missing-sample` must localize to `link.replay_input[pass1]` sample 1000 or the run fails; failed cases retain their raw artifact tree | Met |
| 3 | Parameter shuffle, wrong noise, interpolation, gain, normalization, missing-sample mutations detected | **Met, and all thirteen controls are genuine RTL mutations** (no stimulus-only control remains). Six are demonstrated on the declared binding-distinct stimulus because a uniform one cannot kill them | Met at the chain boundary; four controls stimulus-only |
| 4 | Two clean simulations artifact-hash identical | **Met** for `voice:divide-distinct-levels` at full length, over all six artifact families | Met for two cases |
| 5 | Runtime/regression partition and full evidence commands documented | **Met** (table above, with declared mutation walk caps and measured dev-Mac figures) | Met |
| 6 | Passing result cites exact RTL/fixed-vector/tool commits; no float tolerance | **Met for the `regression` profile.** `sim/evidence/oneshot-whole-voice-regression-v1.json` is committed, cites commit `97ee7dd94d068bd7341f5ee02247e63f99597336`, `git_tree_dirty: false`, `result: PASS`, `float_tolerance: null`; verified committable by `tools/verify_oneshot_evidence.py`. **Not met at `full`** (AC1 at `full` has not run either) | **Not met yet.** A workdir-path defect (see "A discovered defect" below) made generating a clean record impossible before this increment; the fix is uncommitted new work in this diff, so no record this PR produces can cite an already-landed commit. Mechanically unblocked: the exact post-merge command is documented below |
| 7 | Must pass before FPGA/gf180 fit claims | **Not satisfied.** Requires AC1 at `full` and AC6's committed, commit-exact record **for both lanes**. The gate stays closed | Not satisfied |

## What is still open

1. **A committed, commit-exact evidence record for the tail-chain lane's
   `regression` profile.** Mechanically unblocked by this increment (the
   workdir-path fix + `tools/verify_oneshot_evidence.py`), not yet generated
   -- see "A discovered defect" under the tail-chain lane above for the exact
   command. The whole-voice lane's own regression-profile record is committed
   (`sim/evidence/oneshot-whole-voice-regression-v1.json`, citing commit
   `97ee7dd94d068bd7341f5ee02247e63f99597336`).
2. **The `full` profile run** (31 cases) for *both* lanes, which a
   release-era record must cite -- an AWS-box command, not a PR gate.
2b. **A trace-level kill for the VCO pitch wire.** `vco-pitch-wire-swap` is
   killed only on the #73 engine's MIDI-clamp op counter, because the engine
   does not export its C4 MIDI sum and the frequency it integrates is the
   host-replayed `exp2` shadow word. Exporting that sum (and comparing it) is
   an RTL change to a qualified module, so it is a follow-up rather than part
   of this verification increment.
3. Neither lane is folded into the #78 aggregate gate
   (`tools/qualify_rtl_modules.py`): that gate's lane list is derived from
   `tb/run_tb.py`'s choices and its lint ledger is calibrated to CI's
   toolchain, so folding either flow in requires a matching re-baseline. A
   documented follow-up, not a silent skip.
4. `sim-lanes` itself (400-minute cap, 370-minute step sum) exceeds
   GitHub-hosted's 360-minute job limit. Pre-existing on `main` and unchanged
   here; fixing it needs a `sim-lanes` split.

## Honesty

Neither lane makes a synthesis, layout, signoff, hardware-playback or
sound-fidelity claim; neither uses a float tolerance; and no run that did not
execute is reported here as a pass.
