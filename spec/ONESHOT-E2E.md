# One-shot RTL end-to-end conformance (issue #79) -- tail-chain increment

Status: **PARTIAL INCREMENT.** Issue #79 asks for the integrated one-shot top
to be proven bit-identical end to end. The integrated one-shot top does not
exist yet. This record declares what the first integrated composition of
landed RTL *is*, what it proves, and -- with equal weight -- what it does not.
Issue #79 stays open; nothing here releases the "must pass before FPGA/gf180
fit claims" gate.

Owned artifacts:

- `tb/sv/one_shot_tail_top.sv` -- the structural top.
- `tb/sv/tb_one_shot_tail.sv` -- the file-driven testbench.
- `tb/run_oneshot.py` -- the flow (comparison, mutations, hashes, record).
- `tests/test_oneshot_tail_chain.py` -- simulator-free harness checks.

It changes no arithmetic profile, noise policy, normalization, parameter
ordering or clip timing; it adds no decision-record-gated behavior.

## What is composed

```
host-fed streams --> audio_mix_engine (#76) --> normalization_replay_engine (#77)
 raw_vco_1/2/noise     3 VCAs, 3 level mults,     pass 1 peak, strict branch,
 amp_vco_1/2/noise     Q6.42 accumulate, S4       U1.22 reciprocal, pass 2
 level words                                      gain multiply / identity
```

The mixer's registered `mix_word`/`out_valid` pair drives the replay engine's
`mix_in`/`mix_valid` directly (the `link_valid` wire, a named mutation seam).
Pass 1 and pass 2 are two re-triggered mixer walks over identical inputs --
DR-0010 P4's two-pass re-render with no clip buffer. The host drives only the
sequencing signals (`start`, mixer `trigger`, `mix_done`).

## What is NOT composed (the integrated one-shot top is still missing)

- The audio-rate sources (`vco_1.raw`, `vco_2.raw`, `noise.raw`) and the
  endpoint-aligned amplitude columns (`control_upsample.*_amp`) are **host-fed
  from the frozen fixed model's own rows**, exactly as the #76 lane declares
  its inputs. The #70 (ADSR), #71 (LFO/control VCA), #72 (mod matrix +
  upsample), #73 (sine VCO), #74 (square/saw VCO) and #75 (noise) engines are
  not wired into this top, and no whole-source composition of them exists.
- The shadow exp2/tanh sites and the `**alpha` binary64 shadow have no resident
  RTL (open DR-0008/DR-0010 items).
- The serialized single-MAC schedule does not exist; both engines retire one
  sample per cycle.
- Therefore `mixer.output` here is the output of the *tail* of the voice given
  model-derived upstream words, not a rendered one-shot from patch parameters.

## Acceptance-criteria ledger

| # | Criterion | Status | Where / why |
| --- | --- | --- | --- |
| 1 | All required cases produce exactly 176,400 bit-exact samples and exact status/trace sequences | **PARTIAL.** Met for the tail chain (every case in the declared profile, both passes and the output clip, plus every status/counter register). **Not met** for the whole-voice top: it does not exist. | `oneshot()` section 1 |
| 2 | First mismatch localized by trace/cycle/sample, raw artifacts retained | Met for the chain | `oneshot_rows`, `oneshot_print_rows`: three sequence traces are captured -- the mixer's emitted stream, the replay engine's consumed stream (`link.replay_input`, sampled on the `link_valid` seam) and the released output -- and the first mismatch is the earliest by cycle. The missing-sample control must localize to `link.replay_input[pass1]` at its dropped sample (1000) or the run fails. Failed committed runs copy their raw artifacts to a printed `oneshot-failed-*` temp dir; `--workdir` keeps everything |
| 3 | Parameter shuffle, wrong noise, interpolation, gain, normalization, missing-sample mutations detected | Met at the chain boundary, with the caveat below | Mutation table below |
| 4 | Two clean simulations artifact-hash identical | Met for two declared cases (one divide, one bypass); every other case is simulated once | `oneshot()` section 3 |
| 5 | Runtime/regression partition and full evidence commands documented | Met | Partition below |
| 6 | Passing result cites exact RTL/fixed-vector/tool commits; no float tolerance | Partially: see "Evidence identity" | the record cites content sha256 of every RTL/vector file, the git HEAD it was produced on and whether `tb`/`src`/`spec/reference` were dirty; comparisons are exact integer equality, `float_tolerance` is recorded as `null` |
| 7 | Must pass before FPGA/gf180 fit claims | **Not satisfied.** A tail-chain pass does not discharge this gate; it still requires the whole-voice composition. | follow-up issue |

### The mutation caveat (AC3)

"Parameter shuffle", "wrong noise" and "interpolation" are faults of logic
that lives *upstream* of this chain (patch parameter routing, the noise
stream, the control upsample). They are therefore planted as **stimulus-side**
mutations: the pristine RTL is fed a wrong level order / a one-slot-rotated
noise stream / a zero-order-hold amplitude column, and the chain must expose
each against the model's expected traces. That proves the chain is *sensitive*
to those faults, not that RTL implementing those upstream blocks catches them
-- that RTL is not composed here.

`gain-one-ulp` is **also stimulus-side**: it bumps the host-fed noise level
word by one ULP; the mixer's level-multiply RTL is pristine. So four of the
ten controls (`parameter-shuffle`, `wrong-noise`, `interpolation-zoh`,
`gain-one-ulp`) are stimulus-only. The other six -- the four normalization
mutations, `missing-sample` and `mixer-truncate` -- are genuine RTL mutations
of the composed hardware. The only genuine RTL *gain* fault is
`normalization-wrong-reciprocal`; there is no RTL mutant of the mixer's level
multiply in this lane.

| Control | Kind | Planted fault |
| --- | --- | --- |
| `parameter-shuffle` | stimulus | the three mixer level words rotated across lanes |
| `wrong-noise` | stimulus | the noise stream rotated by one sample |
| `interpolation-zoh` | stimulus | endpoint-aligned amplitude columns replaced by a 100-sample zero-order hold |
| `gain-one-ulp` | stimulus | noise level word +1 ULP |
| `normalization-always-off` | RTL | replay engine never divides (on a divide case) |
| `normalization-always-on` | RTL | replay engine always divides (on a bypass case) |
| `normalization-wrong-reciprocal` | RTL | U1.22 gain word +1 ULP |
| `normalization-wrong-peak` | RTL | peak tracker keeps the last sample, not the max |
| `missing-sample` | RTL | one mixer sample (index 1000) dropped at the link; must localize to `link.replay_input[pass1]` sample 1000 |
| `mixer-truncate` | RTL | truncation instead of half-even at the mixer narrowing |

Each must be DETECTED; an undetected control fails the run. Detection is the
exact-comparison rows of the same machinery the committed cases use, and the
first (earliest-cycle) mismatching trace/cycle/sample is printed per control.
`missing-sample` additionally fails the run if it is detected but its first
row is not the declared link trace and sample.

Back-to-back replay note: the replay engine's op counters are free-running
until reset, so the replay check requires the second run's counters to equal
the solo run's plus the first run's; sample streams and all non-counter status
must match the solo run exactly.

## Cases

A "case" is one fixed-model clip pushed through both passes (176,400 mixer
samples each) and the 176,400-sample released output.

- **receipt cases**: the 26 param-committed cases of
  `sim/reference/fixed-voice-golden-v1.json`. Their `mixer.output`,
  `mixer.gain`, `mixer.peak` digests and fixed branch decision are pinned to
  the frozen receipt before the RTL is compared.
- **directed fixtures**: `special:silence`, `special:near-silence`,
  `special:stress` (`spec/reference/directed-voice-v1.json`). No frozen receipt
  pin; the model-vs-mirror equality proof in `mix_derive_case` applies.
- **derived fixture** `oneshot:divide-distinct-levels`: `special:stress` with
  `mixer.vco_2=0.75`, `mixer.noise=0.5` (three distinct level words, divide
  branch, mixer C7 saturation exercised). No receipt pin. Declared because the
  receipt's own divide-branch cases (`global-0/2/52/63`) are digest-custody
  corpus items whose physical parameters are never committed, so no stimulus
  can be built from them.

## Runtime / regression partition and evidence commands

| Profile | Cases | Command | Role |
| --- | --- | --- | --- |
| `regression` (default) | `normalization:above/below/tie`, `normalization-stress:anchor-3.9478583336`, `source:noise`, `special:silence`, `special:stress`, `oneshot:divide-distinct-levels` plus the replay pair, two hash pairs and ten mutation simulations | `python3 tb/run_oneshot.py --profile regression` | PR/CI gate (`tb-sim.yml` job `oneshot-tail-chain`) |
| `full` | all 26 receipt cases + 3 directed fixtures + the derived fixture, plus the same pair/hash/mutation simulations | `python3 tb/run_oneshot.py --profile full --workdir <dir>` | release-era evidence; remote AWS box per `CLAUDE.md` |

Both profiles walk the complete clip twice; neither caps a walk. The
regression profile compiles and simulates roughly twenty-five times; a full
regression run on the dev Mac took tens of minutes wall-clock
(single machine, serial; indicative, not a bound). Pass `--workdir` to retain every raw
artifact (`run0_{params,streams,mixcap,linkcap,outcap,status,ops}.txt`) and the
evidence record `oneshot-evidence.json`. Exit status is the lane's own
pass/fail; exit 3 means Icarus Verilog is absent and **nothing ran**.

Simulator-free harness checks:
`python3 -m unittest tests.test_oneshot_tail_chain`.

Not yet part of the #78 aggregate gate (`tools/qualify_rtl_modules.py`): that
gate's lane list is derived from `tb/run_tb.py`'s choices and its lint ledger
is calibrated to CI's toolchain; folding this flow in requires a matching
re-baseline and is a follow-up, not silently skipped.

## Evidence identity

`oneshot-evidence.json` records `git_head` (the commit the flow ran on),
`git_tree_dirty` (whether `tb/`, `src/` or `spec/reference/` carried
uncommitted changes), the `iverilog` version, and sha256 of every RTL/testbench
source, the constants package, `fixed-voice-golden-v1.json` and
`directed-voice-v1.json`. A record produced from a dirty tree, or whose
`git_head` is not the commit under review, is not citeable evidence. No evidence record is committed with this
increment: a record can only cite its own commit exactly if generated after
that commit exists, so a PR-commit-exact record must be generated on the merged
commit.

## Honesty

This record makes no synthesis, layout, signoff, hardware-playback or
sound-fidelity claim, no float-tolerance claim, and no claim about the
integrated one-shot top, which does not exist.
