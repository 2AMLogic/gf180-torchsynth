# One-shot RTL end-to-end conformance (issue #79)

Status: **the integrated whole-voice one-shot top now exists** and is proven
bit-identical to the frozen fixed model over its declared `regression`
profile, and -- as of the `directed`-profile run recorded below -- over the
three compact directed vectors as well.
**Committed, commit-exact** evidence records now exist for four runs across
the two lanes:
`sim/evidence/oneshot-whole-voice-regression-v1.json` (citing commit
`97ee7dd94d068bd7341f5ee02247e63f99597336`),
`sim/evidence/oneshot-whole-voice-directed-v1.json` (citing commit
`8abf93efb42b359b79eb547ac62c36fd2bec509d`),
`sim/evidence/oneshot-tail-chain-regression-v1.json` (citing commit
`64ffd399b8843658a2a5afc74a9a53ba8ad23e03`, the merge commit of PR #259 that
landed the tail-chain workdir-path fix this record's own generation depended
on) and -- new -- `sim/evidence/oneshot-tail-chain-full-v1.json` (citing
commit `787fd304b7c60ff0cdb3d37211e44ce56587ee48`), the **first
`full`-profile record of either lane**: all 30 tail-chain cases bit-exact,
`ONESHOT RUN PASSED`. See "Evidence identity". All four are re-hashed against
the tree's own RTL and frozen vectors on every CI run, so none can quietly go
stale after an engine edit, and the two tail-chain records are additionally
checked to be *nested* -- the `full` one must cover every case and every
negative control the `regression` one does, or it is not the superseding
record it claims to be. Issue #79 nonetheless stays open on the
**whole-voice** lane's `full`-profile run, which the release-era record must
also cite. See "What is still open" below. Nothing here releases the "must
pass before FPGA/gf180 fit claims" gate.

**The `directed` profile below has been run, has passed, and its record is now
committed.** The run is real: all five committed cases bit-exact, both ends of
the peak envelope reached, 13/13 all-RTL mutations detected, `VOICE RUN
PASSED` (see "The `directed`-profile run" below for the full result). The
first passing `directed` run could not be *committed*, because a record must
be generated on an already-landed clean commit and that run's
`identity.git_head` named the branch commit introducing the profile itself --
a commit that, because this repository squash-merges, never reaches `main`.
The profile has since landed (PR #269), so the run was repeated on the landed
commit `8abf93efb42b359b79eb547ac62c36fd2bec509d` and the resulting record is
citeable. That reachability condition is no longer a rule a reader has to
remember to apply by hand: `tools/verify_oneshot_evidence.py
--require-reachable` now answers it with an exit code, and
`tests/test_committed_oneshot_evidence.py` re-asserts it for every committed
record on every CI run. AC1 and AC6 are therefore both met at `directed`.

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

### The directed vectors, and the peak envelope's low end

Issue #79 asks for the integrated top over "compact directed vectors **and**
the selected regression corpus". The `regression` pair above is only the
second half of that; the three directed fixtures are the first, and until the
`directed` profile existed they were reachable **only** inside the 31-case
`full` profile, which has never been run. So the integrated top's bit-identity
claim had never seen any of them.

That is not a cosmetic gap, because of where they sit on the normalization
peak:

| case | branch | peak word | what it contributes |
| --- | --- | --- | --- |
| `special:silence` | bypass | **0** | the only committed stimulus with a zero peak — the replay controller's zero-peak path, where a reciprocal is undefined |
| `special:near-silence` | bypass | **2** | a two-ULP peak: the smallest non-zero peak of any case either profile plans |
| `special:stress` | divide | 8388608 | the un-overridden parameter set both derived fixtures are built from. Its ceiling peak is **also** reached by `voice:divide-distinct-levels`, so this vector widens the parameters, not the peak range |

The two regression cases reach the ceiling end but **not** the low end:
`normalization:below` bypasses at exactly unity (`2^21`) and
`voice:divide-distinct-levels` saturates to the ceiling. Neither has a zero or
near-zero peak. Only the low end is exclusive to the directed vectors, and
only that is claimed — `tests/test_oneshot_voice.py` asserts both halves of
that statement (zero is unreachable from `regression`, the ceiling *is*
reachable from it) against the frozen fixtures, so the claim cannot rot into a
comment that used to be true. The first draft of this increment overstated it
in the other direction and that check is what caught it.

**The low end is a pass condition, not a happy accident.** In any profile that
plans a directed vector, the flow requires the peak envelope's ends to have
actually been reached:

```
peak envelope (3 directed vectors planned): zero peak True, ceiling peak
8388608 True, observed [...] -> OK
```

and fails the run otherwise. The ceiling is **derived** from the accepted
audio format (`-formats.audio.min_int`, the magnitude of the Q2.21 word's own
negative rail), never a literal, so a format change moves the gate with it
rather than silently passing a stale constant. In `regression`, where no
directed vector is planned, the flow prints the observed peaks and says
plainly that the envelope's ends are proven by the `directed` and `full`
profiles and not there — an unrun check is never reported as a pass.

The `directed` profile is a strict **superset** of `regression`: every
regression case, the binding baseline, the two-clean-simulation hash identity,
the back-to-back replay pair and all thirteen all-RTL mutations run unchanged,
and the three directed vectors are added as further full-length committed
cases. Superset rather than a narrow extra lane on purpose: a profile that
widened case coverage while dropping the mutation lane would produce a record
that reads stronger and proves less. `tests/test_oneshot_voice.py` enforces
the superset relation, that the three vectors are exactly the declared
`MIX_FIXTURE_CASES`, and that no case is planned twice.

### The `directed`-profile run

`python3 tb/run_voice.py --profile directed --workdir <dir>`, Icarus Verilog
13.0, **`VOICE RUN PASSED`** (exit `0`). Every stage ran; none was skipped.

| Case | Branch | Peak word | Gain word | Receipt-pinned | Result |
| --- | --- | --- | --- | --- | --- |
| `normalization:below` | bypass | 2097152 | 4194304 | yes | PASS |
| `voice:divide-distinct-levels` | divide | 8388608 | 1048576 | no | PASS |
| `special:silence` | bypass | **0** | 4194304 | no | PASS |
| `special:near-silence` | bypass | **2** | 4194304 | no | PASS |
| `special:stress` | divide | 8388608 | 1048576 | no | PASS |

Each of the five is 1,764 control ticks plus two complete 176,400-sample
audio passes, compared bit-exact across 28 named traces plus every
status/error register, `pass_index` and all sixteen engines' op counters. On
top of that: branch coverage both ways; the **peak-envelope gate armed and
satisfied** (`zero peak True, ceiling peak 8388608 True, observed [0, 2,
2097152, 8388608, 8388608]`); the `voice:binding-distinct` pristine baseline
bit-exact over the declared prefix; two clean full-length simulations
artifact-hash identical over all six artifact families; the no-reset
back-to-back replay pair reproducing its solo run; and **13/13 all-RTL
mutations `DETECTED`**, each localized to a trace/cycle/sample -- or, for
`vco-pitch-wire-swap`, to `ops.V1.1[5]` (0 against 5,683), the op counter
that is its only observable, exactly as declared above. No float tolerance
anywhere.

So the three directed vectors have now actually been walked through the
integrated top, and `special:silence` has actually exercised the replay
controller's zero-peak path. That is AC1's "compact directed vectors" clause,
and it is the thing no previous increment could say.

**The record is committed:
`sim/evidence/oneshot-whole-voice-directed-v1.json`.** Getting there took the
run twice, and the reason is worth keeping on the record. The *first* passing
`directed` run was clean in every respect a record can describe --
`identity.git_tree_dirty` `false`, every digest fresh, `result: PASS`,
`float_tolerance: null` -- yet its `identity.git_head` named the branch commit
that introduced the `directed` profile itself. Because this repository
squash-merges, that commit never reaches `main`, so the record cited an object
no reader can resolve: the same structural bar PR #259 hit for the tail chain
and PR #262 cleared by re-running on the already-landed merge commit. The
profile has since landed (PR #269), so the run above was repeated on the
landed commit `8abf93efb42b359b79eb547ac62c36fd2bec509d` with a clean tree,
and that run's record is the committed one. Its figures are the table above,
reproduced independently of the first attempt: the same five PASSes, the same
peak envelope, the same 13/13 detections. Wall-clock for the eleven
simulations on an 8-core Linux box under light other load: 43m09s.

**The reachability condition is now mechanical, not a rule a reader must
remember.** `tools/verify_oneshot_evidence.py` checks dirtiness, result, float
tolerance and digest freshness -- all four of which the uncommittable first
record passed -- and, with `--require-reachable`, additionally asks `git`
whether `identity.git_head` is an ancestor of the default branch:

```
python3 tools/verify_oneshot_evidence.py \
    sim/evidence/oneshot-whole-voice-directed-v1.json \
    --expect-head 8abf93efb42b359b79eb547ac62c36fd2bec509d --require-reachable
COMMITTABLE: ... git_head 8abf93efb42b359b79eb547ac62c36fd2bec509d,
digests fresh against <tree>, git_head reachable on origin/main
```

Without the flag the tool now *says* that reachability was not checked rather
than letting a bare `COMMITTABLE` imply it. Every way of not knowing the
answer -- a shallow clone that lacks the commit, a checkout with no
default-branch ref, a path that is not a repository -- is an error, never a
silent pass, because a check that could not run must never look like one that
passed. `tests/test_committed_oneshot_evidence.py` re-asserts the condition for
every committed record on every CI run, which is why `.github/workflows/ci.yml`
checks out at `fetch-depth: 0`: the default depth-1 fetch cannot answer an
ancestry question, and the check fails rather than passing vacuously if that
depth is ever reverted.

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
`voice-failed-*` temp dir; `--workdir` retains everything unconditionally. A
*corrupt* capture is reported separately and never as a localized mismatch —
see "A corrupt capture is inconclusive, not a verdict" below.

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
| `directed` | **everything `regression` runs**, plus the three directed vectors (`special:silence`, `special:near-silence`, `special:stress`) as full-length committed cases, with the peak-envelope gate armed | `python3 tb/run_voice.py --profile directed --workdir <dir>` | the "compact directed vectors" half of issue #79's AC1. One session's work on an 8-core box; too slow for the PR gate, far cheaper than `full`. **Run, passed, and committed** as `sim/evidence/oneshot-whole-voice-directed-v1.json` (43m09s wall-clock) -- see "The `directed`-profile run" |
| `full` | all 26 receipt cases + 3 directed fixtures + both derived fixtures, same baseline/pair/hash/mutation simulations | `python3 tb/run_voice.py --profile full --workdir <dir>` | release-era evidence; remote AWS box per `CLAUDE.md` |

All three profiles walk the complete clip twice for every committed case; none
caps a committed walk. The profiles are **nested** — `regression` ⊂ `directed`
⊂ `full` on committed cases, with every stage shared — so a `directed` record
supersedes a `regression` one rather than sitting beside it, and no reader has
to compare two records to work out which checks ran. Simulator-free harness
checks: `python3 -m unittest tests.test_oneshot_voice`.

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

Exit status is the lane's own pass/fail, and two of the four statuses are
**not verdicts**:

| Exit | Meaning |
| --- | --- |
| `0` | pass |
| `1` | a real bit-identity or mutation-sensitivity failure — a verdict against the RTL |
| `3` | Icarus Verilog is absent and **nothing ran** — never reported as a pass |
| `4` | a capture file was corrupt, so the run is **INCONCLUSIVE** — see below |

### A corrupt capture is inconclusive, not a verdict

`CLAUDE.md` requires that a check which did not run is never reported as a
pass. The corollary, which this lane now enforces, is that it must not be
reported as a **failure of the RTL** either.

The bench's capture `$fwrite` has a fixed column count, so a row with the
wrong number of fields cannot come from the simulator. It can only come from
bytes lost between that `$fwrite` and the file — a writeback that failed on a
full or failing filesystem. When a block is dropped mid-file the surviving
bytes splice the prefix of one row onto the suffix of a much later one, and
the resulting field count is wrong. `voice_capture_rows` detects exactly that
signature and raises `VoiceCaptureIntegrityError`, which the flow turns into
exit `4` with a diagnosis naming the file, the line, both field counts, the
file's byte and row totals, and the sentence "not an RTL/model disagreement".
**No evidence record is written**, because none was earned.

This is not hypothetical. The first `directed`-profile run lost **9,987 of
352,800** `audiocap` rows in five spliced holes (25,761,768 bytes against the
26,286,160 the same case had written complete, and compared bit-exact,
twenty-five minutes earlier in the same run) on a shared 8-core box. The
flow's reaction at the time was the opaque message `audiocap row '...' does
not carry 15 fields` — which a reader would reasonably misread as an RTL
defect, and which is the misdiagnosis this change exists to prevent. The
retry on the same box, with a core and some disk headroom free, passed every
stage (see "The `directed`-profile run"), which is the outcome that confirms
the first run's reading: the RTL was never implicated, the filesystem was.

Two scope limits, stated rather than glossed:

- an `x`-state emission has the *right* field count and **is** a genuine
  mismatch; the comparator still owns it. Conflating the two would let a real
  RTL defect hide behind "inconclusive".
- a lost block that happened to land exactly on row boundaries would leave a
  well-formed but **short** file, which is not distinguishable from a bench
  that stopped early. That still surfaces as the case's count mismatch. Only
  the spliced-row signature is diagnosable, and only that is claimed.

### Evidence identity

`voice-evidence.json` (schema
`gf180-torchsynth/oneshot-whole-voice-evidence-v1`) is written on every run and
uploaded by the CI lane as the `oneshot-whole-voice-evidence` artifact. It
records `git_head`,
`git_tree_dirty` (whether any path in the declared evidence scope
`tb/`, `src/`, `spec/reference/`, `sim/reference/` carried uncommitted
changes — `run_tb.EVIDENCE_TREE_SCOPE`), the `iverilog` version banner, and
sha256 of every RTL
and testbench source, the constants package,
`fixed-voice-golden-v1.json` and `directed-voice-v1.json`, plus every
mutation verdict with its declared walk length and `float_tolerance: null`.

Three further fields make a record say *which stimulus* produced the pass
rather than only how many cases did, so AC1's "all required cases" is
auditable from the record alone:

| Field | Contents |
| --- | --- |
| `case_diagnostics` | per committed case: `branch`, `peak_word`, `gain_word`, whether it is `frozen_pinned` to a receipt entry, and its own `PASS`/`FAIL` |
| `directed_cases` | which of the three declared directed vectors this run actually planned (empty in `regression`) |
| `peak_envelope` | `zero_peak`, `ceiling_peak`, the derived `ceiling_peak_word`, and the sorted `observed_peaks` of every committed case |

The schema name is unchanged: these are additive fields, so
`tools/verify_oneshot_evidence.py` and the two already-committed
`regression` records keep validating untouched. A reader comparing a
`directed` record against a `regression` one can see from `directed_cases`
and `peak_envelope` alone which of the two is the stronger claim.

**The dirtiness scope must contain every digested input, and now does.**
`sim/reference/` was originally missing from it even though
`fixed-voice-golden-v1.json` — the frozen receipt both lanes pin their expected
digests against — lives there, so an uncommitted receipt edit could have
produced a record still reporting `git_tree_dirty: false`. Both flows now take
the scope from one shared constant, and
`tests/test_committed_oneshot_evidence.py` asserts that every source a
committed record digests lies under one of its prefixes, so adding a digested
input outside the scope fails CI instead of silently reopening the gap. The two
`regression` records were re-checked against the blobs at the commits they cite
(`97ee7dd9…`, `64ffd399…`): every digest matches, so the widened scope does not
retroactively weaken either record. The `directed` record postdates the
widening and was generated under the corrected scope.

**A record produced from a dirty tree, or whose `git_head` is not the commit
under review, is not citeable evidence.** Note that on a `pull_request` event
`actions/checkout@v4` checks out the PR's *merge* commit, so a CI-produced
record's `git_head` names that merge commit rather than the branch head — still
exact, but a different object than a local run on the same branch.

**A record also stops being evidence when the sources it names change, and
none of its own fields notice.** Editing an engine leaves the committed record
untouched: `result` stays `PASS`, `git_tree_dirty` stays `false`, and the cited
`git_head` keeps naming a commit whose RTL is no longer the RTL in the tree —
so a changed engine would inherit the previous version's bit-identity proof.
`tools/verify_oneshot_evidence.py` therefore re-hashes every source named in
`identity.rtl_sha256` / `identity.fixed_vector_sha256` against the tree being
checked (on by default; `--skip-tree-check` disables it and says so loudly in
the output and in the exit narration). A name that resolves to no file, or to
more than one under the declared roots (`tb/sv`, `spec/reference`,
`sim/reference`), is an error rather than a skip.
`tests/test_committed_oneshot_evidence.py` runs that same check against every
committed record on every CI run, and proves the gate bites by planting a
one-byte change in a throwaway mirror tree. **A stale record is regenerated,
never re-pinned by hand** — the failure message prints the lane's own
regeneration command, with the record's **own** profile substituted in rather
than a hardcoded `regression`.

**A record's filename may not overstate the run inside it.** Committed records
are named `oneshot-<lane>-<profile>-v1.json`, and the filename is what a
reader greps and what the acceptance-criteria ledger below cites — but
`profile` inside the JSON is what the flow actually planned, and until the
whole-voice flow had more than one profile nothing tied the two together.
Because the profiles are nested (`regression` ⊂ `directed` ⊂ `full`), the
mistake that matters has a direction: a `regression` run filed as
`oneshot-whole-voice-full-v1.json` would read as the strictly stronger proof
while containing the weaker one, and every digest in it would still verify.
`tests/test_committed_oneshot_evidence.py` now refuses that disagreement
(proving it discriminates on a mirror copy filed under the wrong name), and
additionally requires each record's `profile` to be one its own flow's CLI
accepts — so a record can never name a profile nobody can regenerate it from.

**A record must also cite a commit a reader can actually resolve, and that is
now checked rather than asked for.** Every condition above is satisfied by a
record produced on a pristine *pull-request branch* commit -- and because this
repository squash-merges, a branch's own commits never reach `main`, so such a
record cites a SHA that resolves for nobody but the agent that produced it.
That is not hypothetical: the first passing `directed` run was held back for
exactly this reason, with this document able only to tell a reader to check
reachability by hand. `tools/verify_oneshot_evidence.py --require-reachable`
now asks `git` whether `identity.git_head` is an ancestor of the default
branch (`origin/main`, or an explicit `--default-branch-ref`), and
`tests/test_committed_oneshot_evidence.py` re-asserts it for every committed
record on every CI run -- which is why `.github/workflows/ci.yml` checks out at
`fetch-depth: 0`. Each way of *not knowing* the answer is reported as an error
with its remedy, never as a pass: a clone that lacks the commit, a checkout
with no default-branch ref, a path that is not a repository. Without the flag
the tool prints a note saying reachability was **not** checked, so a bare
`COMMITTABLE` can no longer be read as implying it.

**Two committed whole-voice records now exist**, one per run profile:
`sim/evidence/oneshot-whole-voice-regression-v1.json` and
`sim/evidence/oneshot-whole-voice-directed-v1.json`. The `directed` one was
generated on the already-landed commit
`8abf93efb42b359b79eb547ac62c36fd2bec509d` with a clean tree, and verified by
`python3 tools/verify_oneshot_evidence.py sim/evidence/oneshot-whole-voice-directed-v1.json --expect-head 8abf93efb42b359b79eb547ac62c36fd2bec509d --require-reachable`
(exit 0, `COMMITTABLE`, `git_head reachable on origin/main`) before being
added here. The `regression` one was generated on
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
creates at merge time does, and no one can know that SHA in advance). Both
later records cleared that bar the same way: the tail-chain record waited for
PR #259's workdir-path fix to land and then cited that merge commit, and the
`directed` record waited for PR #269 to land the profile and then cited the
landed commit. Each time, the sequence was *land the enabling change, then
re-run on the landed commit* -- and `--require-reachable` is what now makes
skipping it a failing check rather than an oversight. The tail chain's
`full`-profile record cleared it the same way a fourth time, on the landed
commit `787fd304b7c60ff0cdb3d37211e44ce56587ee48`; the whole-voice lane's
still-missing `full` record faces the same requirement again, as every record
does: it must be generated on an already-existing, clean, already-landed
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

| Profile | Cases | Command | Role |
| --- | --- | --- | --- |
| `regression` (default) | 8 | `python3 tb/run_oneshot.py --profile regression` | PR/CI gate (`tb-sim.yml` job `oneshot-tail-chain`) |
| `full` | 30 | `python3 tb/run_oneshot.py --profile full --workdir <dir>` | release-era evidence. **Run, passed, and committed** -- see "The `full`-profile run" below |

The two are **nested**: `full` plans every `regression` case plus the other 22,
and shares every later stage (the replay pair, the two hash simulations, all
ten negative controls) unchanged. So the `full` record supersedes the
`regression` one rather than sitting beside it, and
`tests/test_committed_oneshot_evidence.py` asserts that nesting against the
two committed records -- a `full` record that covered fewer cases, or
demonstrated fewer controls, than the `regression` one would be refused rather
than read as the stronger proof its filename claims.

Simulator-free harness checks:
`python3 -m unittest tests.test_oneshot_tail_chain`.

### The `full`-profile run

`python3 tb/run_oneshot.py --profile full --workdir <dir>`, Icarus Verilog
13.0, **`ONESHOT RUN PASSED`** (exit `0`). Every stage ran; none was skipped.

All **30** cases -- the 26 param-committed receipt cases, the three directed
fixtures (`special:silence`, `special:near-silence`, `special:stress`) and the
derived `oneshot:divide-distinct-levels` -- walked 176,400 samples x 2 passes
and compared bit-exact across every named trace, status/error register and op
counter. Both normalization branches covered (28 bypass, 2 divide). Observed
peak words span **0** (`special:silence`, the zero-peak path where a
reciprocal is undefined) through **2** (`special:near-silence`) to the ceiling
**8388608** (`special:stress` and `oneshot:divide-distinct-levels`, both on the
divide branch). The no-reset back-to-back replay pair reproduced its solo run;
two clean full-length simulations of each of `oneshot:divide-distinct-levels`
and `normalization:below` were artifact-hash identical over all seven files;
and **10/10 mutations `DETECTED`**, each localized to a trace/cycle/sample.
No float tolerance anywhere.

Total wall-clock: **15 min 20 s** on a shared 8-core x86-64 Linux dispatch box
(Icarus 13.0, serial, run niced, other sweeps live on the same cores, root
filesystem at ~81% used) -- 46 full-length Icarus simulations (30 committed
cases in the first 9 min 4 s, so ~18 s each, then the replay pair and its
solo, four hash simulations and ten mutation simulations), retaining **831 MB**
of raw artifacts under `--workdir`. Indicative figures, not bounds. This is
roughly 3.7x the `regression` profile's committed-case work, which is why this
lane's `full` profile is affordable in one session while the whole-voice
lane's -- whose single cases cost ~4 min each rather than ~18 s -- is not.

Its record is committed as `sim/evidence/oneshot-tail-chain-full-v1.json`,
citing commit `787fd304b7c60ff0cdb3d37211e44ce56587ee48` -- the already-landed
tip of `main` the run was made on, with a clean tree, before any file in this
increment's diff was written. Verified by

```
python3 tools/verify_oneshot_evidence.py \
    sim/evidence/oneshot-tail-chain-full-v1.json \
    --expect-head 787fd304b7c60ff0cdb3d37211e44ce56587ee48 --require-reachable
```

(exit 0, `COMMITTABLE`, `git_head reachable on origin/main`) before being
added here.

**What this record does not establish.** It is the tail chain's own scope and
nothing wider: `audio_mix_engine` -> `normalization_replay_engine` over
host-fed source and amplitude streams, with four of its ten negative controls
stimulus-only (see the caveat below). It is **not** a whole-voice
`full`-profile result, and the FPGA/gf180 gate stays closed on that one -- see
"What is still open".

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

That fix was itself new, uncommitted work in the increment that discovered
it (PR #259), so no tail-chain evidence record generated on a tree containing
it could be a clean citation of an already-landed commit
(`identity.git_tree_dirty` was correctly `true` on every run that increment
made). PR #259 merged as commit `64ffd399b8843658a2a5afc74a9a53ba8ad23e03`,
mechanically unblocking exactly the command it had documented:

```
python3 tb/run_oneshot.py --profile regression --workdir <absolute-or-relative-dir>
python3 tools/verify_oneshot_evidence.py <dir>/oneshot-evidence.json --expect-head <that commit>
# only if the verifier exits 0:
cp <dir>/oneshot-evidence.json sim/evidence/oneshot-tail-chain-regression-v1.json
```

**This has now been done.** `sim/evidence/oneshot-tail-chain-regression-v1.json`
is committed: generated by `python3 tb/run_oneshot.py --profile regression
--workdir /tmp/oneshot-tailchain-evidence` on a worktree freshly branched
from `64ffd399b8843658a2a5afc74a9a53ba8ad23e03` (`git status` clean both
before and after the run -- the flow writes only under `--workdir`), verified
committable by `python3 tools/verify_oneshot_evidence.py
/tmp/oneshot-tailchain-evidence/oneshot-evidence.json --expect-head
64ffd399b8843658a2a5afc74a9a53ba8ad23e03` (exit 0, `COMMITTABLE`), then
copied in verbatim. It reports `result: PASS`, both branches covered, and
10/10 mutations `DETECTED` -- the same regression-profile result `tb/
run_oneshot.py` has reported since PR #253, now pinned as a committed
artifact. Total Icarus 13.0 wall-clock on the dev Mac: 16m07s.

---

## 3. Acceptance-criteria ledger (issue #79)

| # | Criterion | Whole-voice lane | Tail-chain lane |
| --- | --- | --- | --- |
| 1 | All required cases produce exactly 176,400 bit-exact samples and exact status/trace sequences | **Met for the declared `regression` profile** (both cases, 1,764 control ticks + 2 x 176,400 samples over 27 named traces + the output clip + every status/op counter). **Also met for the `directed` profile**, which discharges the criterion's "compact directed vectors" half: `special:silence` / `special:near-silence` / `special:stress` have each been walked through the integrated top at full length and compared bit-exact, with the peak-envelope gate armed and both ends reached -- `VOICE RUN PASSED`, see "The `directed`-profile run", and now pinned as a committed record. **Not yet run at `full`.** | **Met at `full`** for the tail chain's own scope: all 30 cases (26 receipt + 3 directed fixtures + 1 derived) walked 176,400 samples x 2 passes bit-exact with exact status/trace sequences, `ONESHOT RUN PASSED` -- see "The `full`-profile run". Still the tail chain only, never the whole-voice top |
| 2 | First mismatch localized by trace/cycle/sample, raw artifacts retained | **Met.** `voice_rows` / `voice_print_rows`; rows ordered by cycle; `missing-sample` must localize to `link.replay_input[pass1]` sample 1000 or the run fails; failed cases retain their raw artifact tree | Met |
| 3 | Parameter shuffle, wrong noise, interpolation, gain, normalization, missing-sample mutations detected | **Met, and all thirteen controls are genuine RTL mutations** (no stimulus-only control remains). Six are demonstrated on the declared binding-distinct stimulus because a uniform one cannot kill them | Met at the chain boundary; four controls stimulus-only |
| 4 | Two clean simulations artifact-hash identical | **Met** for `voice:divide-distinct-levels` at full length, over all six artifact families | Met for two cases, at both `regression` and `full` |
| 5 | Runtime/regression partition and full evidence commands documented | **Met** (table above, with declared mutation walk caps and measured dev-Mac figures) | Met, now with measured `full`-profile wall-clock and the nesting relation between its two profiles stated and tested |
| 6 | Passing result cites exact RTL/fixed-vector/tool commits; no float tolerance | **Met for the `regression` profile.** `sim/evidence/oneshot-whole-voice-regression-v1.json` is committed, cites commit `97ee7dd94d068bd7341f5ee02247e63f99597336`, `git_tree_dirty: false`, `result: PASS`, `float_tolerance: null`; verified committable by `tools/verify_oneshot_evidence.py`, and its 15 cited sources are re-hashed against the tree on every CI run, so an engine edit cannot inherit this pass. **Also met for the `directed` profile:** `sim/evidence/oneshot-whole-voice-directed-v1.json` is committed, cites the already-landed commit `8abf93efb42b359b79eb547ac62c36fd2bec509d`, `git_tree_dirty: false`, `result: PASS`, `float_tolerance: null`; verified by `tools/verify_oneshot_evidence.py … --require-reachable` (exit 0), which additionally establishes that the cited commit is reachable on `origin/main` -- the one condition the earlier, uncommittable `directed` record failed. **Not met at `full`** (AC1 has not run there) | **Met for the `regression` profile.** `sim/evidence/oneshot-tail-chain-regression-v1.json` is committed, cites commit `64ffd399b8843658a2a5afc74a9a53ba8ad23e03` (PR #259's merge commit, which landed the workdir-path fix this record's generation depended on), `git_tree_dirty: false`, `result: PASS`, `float_tolerance: null`; verified committable by `tools/verify_oneshot_evidence.py`, with the same per-CI-run re-hash of its 7 cited sources. **Also met at `full`:** `sim/evidence/oneshot-tail-chain-full-v1.json` is committed, cites the already-landed commit `787fd304b7c60ff0cdb3d37211e44ce56587ee48`, `git_tree_dirty: false`, `result: PASS`, `float_tolerance: null`; verified by `tools/verify_oneshot_evidence.py … --expect-head … --require-reachable` (exit 0, reachable on `origin/main`), and asserted to be a strict superset of the `regression` record it supersedes |
| 7 | Must pass before FPGA/gf180 fit claims | **Not satisfied.** AC1's directed-vector half is discharged and AC6 is now met at both `regression` and `directed`, but the gate additionally requires AC1 **and** AC6 at `full` for **both lanes**, and the whole-voice lane has never run at `full`. The gate stays closed | **Satisfied for this lane's own scope** at `full`, which is the narrower claim it has always been: the tail chain is the mixer -> replay-controller chain over host-fed streams, with four stimulus-only controls. The *gate* is not released, because it is a whole-voice gate and that lane's `full` run is still outstanding |

## What is still open

1. **The whole-voice lane's `full` profile run** (31 cases) and the
   committed, commit-exact evidence record it must produce -- not a PR gate
   (tracked in #261), and roughly fifteen times the committed-case work of
   `regression`. Because the profiles are nested, a `full` record supersedes
   both the `directed` and `regression` ones.

   **The tail-chain half of this item is now done**, which is what narrows it
   from "both lanes" to one: `sim/evidence/oneshot-tail-chain-full-v1.json`
   (citing commit `787fd304b7c60ff0cdb3d37211e44ce56587ee48`) is committed,
   all 30 cases bit-exact, 10/10 controls detected, 15 min 20 s of Icarus 13.0
   on a shared 8-core box. Four records are now committed in total; the
   whole-voice `full` profile is the only planned run with none:
   `sim/evidence/oneshot-whole-voice-regression-v1.json` (citing commit
   `97ee7dd94d068bd7341f5ee02247e63f99597336`),
   `sim/evidence/oneshot-whole-voice-directed-v1.json` (citing commit
   `8abf93efb42b359b79eb547ac62c36fd2bec509d`),
   `sim/evidence/oneshot-tail-chain-regression-v1.json` (citing commit
   `64ffd399b8843658a2a5afc74a9a53ba8ad23e03`) and
   `sim/evidence/oneshot-tail-chain-full-v1.json`.

   The sequence the remaining record must follow is the one the `directed` and
   tail-chain `full` records both executed, and it is mechanically enforced
   rather than narrated:

   ```
   python3 tb/run_voice.py --profile full --workdir <dir>      # on an already-landed, clean commit
   python3 tools/verify_oneshot_evidence.py <dir>/voice-evidence.json \
       --expect-head <the landed commit> --require-reachable
   # only if that exits 0:
   cp <dir>/voice-evidence.json sim/evidence/oneshot-whole-voice-full-v1.json
   ```

   `--require-reachable` is what makes the "already-landed" part a check
   instead of a rule to remember. Once copied in, the record needs an entry in
   `tests/test_committed_oneshot_evidence.py`'s `COMMITTED_RECORDS` -- that
   suite refuses an undeclared record, and separately refuses a record whose
   filename and `profile` disagree, so a `-full-v1` name cannot end up over a
   weaker run -- and, since the tail chain became the first lane with two
   committed profiles, also refuses a `full` record that covers fewer cases or
   demonstrates fewer negative controls than the `regression` one it claims to
   supersede.

   **Run it somewhere with headroom.** The *first* `directed` attempt was
   reported INCONCLUSIVE (exit `4`) rather than passed or failed: a shared
   8-core box at ~91% full lost 9,987 `audiocap` rows to a failed writeback
   partway through. That is the incident the corrupt-capture guard was written
   for. `directed` is eleven full-length simulations and about 43 minutes of
   simulator time on a lightly-loaded 8-core box; the whole-voice `full`
   profile is roughly fifteen times the committed-case work, so it wants a
   machine not simultaneously running other sweeps. The tail chain's `full`
   run, by contrast, fit in 15 min 20 s on a *shared* box at ~81% disk with
   ~831 MB retained -- that lane is cheap enough not to need the same care
   (and it did not get it: it ran alongside other live sweeps), and the
   contrast is why the two halves of this item separated rather than landing
   together.
2. **A trace-level kill for the VCO pitch wire** (tracked in #263).
   `vco-pitch-wire-swap` is
   killed only on the #73 engine's MIDI-clamp op counter, because the engine
   does not export its C4 MIDI sum and the frequency it integrates is the
   host-replayed `exp2` shadow word. Exporting that sum (and comparing it) is
   an RTL change to a qualified module, so it is a follow-up rather than part
   of this verification increment.
3. Neither lane is folded into the #78 aggregate gate
   (`tools/qualify_rtl_modules.py`, tracked in #264): that gate's lane list is
   derived from
   `tb/run_tb.py`'s choices and its lint ledger is calibrated to CI's
   toolchain, so folding either flow in requires a matching re-baseline. A
   documented follow-up, not a silent skip.
4. `sim-lanes` itself (400-minute cap, 370-minute step sum) exceeds
   GitHub-hosted's 360-minute job limit (tracked in #265). Pre-existing on
   `main` and unchanged
   here; fixing it needs a `sim-lanes` split.

## Honesty

Neither lane makes a synthesis, layout, signoff, hardware-playback or
sound-fidelity claim; neither uses a float tolerance; and no run that did not
execute is reported here as a pass. In particular:

- the `directed` profile **has** run and passed, and that run is reported above
  with its own per-case numbers rather than as a bare "5 cases passed". Its
  record is now committed, and it is the record of a run on an already-landed
  commit -- not the earlier, uncommittable one, which cited a commit that will
  never exist on `main`. The committed record is the *second* execution of the
  profile, and this document says so rather than quietly presenting one run's
  numbers under the other's SHA;
- the `full` profile has now produced a result **for the tail chain only**,
  reported above with its own case count, branch split, peak span, control
  verdicts and wall-clock rather than as a bare "30 cases passed". For the
  **whole-voice** lane `full` is still **machinery, not a result**: it has
  never been run and has no record. Nothing above is a whole-voice
  `full`-profile claim, and the FPGA/gf180 gate stays closed on it. A
  tail-chain `full` pass is not a smaller version of a whole-voice one -- it
  covers two engines over host-fed streams, with four stimulus-only controls,
  and no amount of case coverage in that lane substitutes for the integrated
  top;
- the first `directed` attempt is recorded as INCONCLUSIVE, which is neither a
  pass nor a verdict against the RTL;
- `--require-reachable` is a check on a record's *citation*, not on the RTL. It
  establishes that a reader can resolve the commit a record names; it adds no
  bit-identity evidence of its own.
