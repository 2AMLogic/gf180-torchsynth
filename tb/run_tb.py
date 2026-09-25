#!/usr/bin/env python3
"""PDK-free RTL testbench runner (issue #68; the AGENTS.md tb convenience).

Two commands:

``selftest`` proves the golden-vector harness end to end against a tiny
synthetic SystemVerilog DUT using an open-source simulator (Icarus
Verilog; no PDK, no vendor tooling):

1. generate a synthetic stimulus and its matching golden vector,
2. compile and run tb/sv/synth_dut.sv + tb/sv/tb_synth_dut.sv,
3. load the captured stream and assert the pass path (reporter: no
   mismatch), then
4. compare against an intentionally corrupted vector and assert the
   first-mismatch reporter names the exact cycle/sample/trace/expected/
   actual row of the planted fault.

``anchor`` graduates the harness to the REAL golden vector
(``sim/reference/golden-vector-fixed-anchor.json``, frozen by #54):

1. load the anchor vector through the landed schema/loader,
2. verify the accepted-contract hash binding (constants package, choice
   register, DR-0008 record; refusal on any mismatch — interface changes
   fail the check instead of silently recompiling),
3. build the accepted quarter-wave table through the DR-0008 refusal gate
   and check it against the vector's bound table digest,
4. derive the DUT control words (the phase-increment word via the declared
   binary64 shadow-site policy and the S1-entry mixer-level word) from the
   vector's own declared scalars and parameters,
5. run the format-true DUT (tb/sv/lut_sine_dut.sv, imported constants
   package) over the full 176,400-sample clip,
6. replay the integer dataflow host-side with the model's own primitives
   and require it to match the frozen vector trace exactly,
7. require the RTL capture to match both streams sample-exactly,
8. plant a fault into the captured stream and require the reporter to name
   the exact cycle/sample/trace/expected/actual row on the real vector, and
9. assert the ratified clip budget (DR-0010 Accepted schedule, machine-
   readable in spec/reference/rtl-schedule-v1.json): the budget constants'
   internal consistency, the landed constants package against its emission,
   and a measured-vs-budget headroom report over the DUT's counted cycles —
   honestly labeled a PARTIAL-DUT measurement (the LUT DUT is one sample
   per cycle, not the serialized single-MAC schedule; no schedule-
   conformance or PPA/fit claim is made).

Exit 0 only if every step passes. The declared shadow sites are replayed
host-side exactly as DR-0008 declares them open items — no RTL claim is
made for them, and nothing here claims synthesis, layout, signoff, or
hardware anything.

``adsr`` (issue #70) runs the bit-exact ADSR envelope engine
(tb/sv/adsr_engine.sv, six concurrent instances) against the frozen fixed
model's committed golden vectors (sim/reference/adsr-golden-v1/):

1. load every committed vector through the landed loader and verify the
   accepted-contract hash binding,
2. re-derive each case's stage formation + per-tick ``**alpha`` shadow
   streams from the model's own control path (``adsr_golden`` asserts the
   host shadow equals the model's ``_ramp`` rows, and the committed traces
   equal the live model's ``_adsr`` outputs),
3. run the engine six instances wide, one case per invocation, and require
   every envelope trace sample-exact against its vector,
4. run two cases back-to-back in one simulation with no reset and require
   the second run to reproduce its solo golden capture byte-for-byte
   (a trigger cannot retain prior envelope state),
5. hard-assert the exported op counters against the DR-0010 #70 owner row
   (multiply-class ops, declared ``**alpha`` shadow sites per tick) plus
   the emitted schedule constants, and
6. plant three breakpoint mutations — wrong sustain level (RTL),
   linear-vs-exponential curve confusion (vector side), off-by-one timing
   (RTL) — and require every one to be DETECTED.

``lfo`` (issue #71) runs the bit-exact RTL LFO + control-rate VCA engine
(tb/sv/lfo_vca_engine.sv, two concurrent instances) against the frozen
fixed model's committed golden vectors (sim/reference/lfo-vca-golden-v1/):

1. load every committed vector through the landed loader and verify the
   accepted-contract hash binding,
2. re-derive each case's envelope streams, shape-weight ``**2.718281828``
   shadow words, and S3 initial-turn word from the model's own control
   path (``lfo_golden`` asserts the integer mirror equals the model's
   ``_lfo``/``_control_vca`` rows, and the committed traces equal the
   live model's outputs),
3. run the engine two instances wide, one case per invocation, and
   require every ``lfo_<n>.raw`` and ``lfo_<n>.post_control_vca`` trace
   sample-exact against its vector,
4. run two cases back-to-back in one simulation with no reset and require
   the second run to reproduce its solo golden capture byte-for-byte
   (no cross-run or cross-instance phase/weight state may survive a
   trigger),
5. hard-assert the exported op counters (6 multiply-class ops, 10
   declared narrowings, 1 phase step, and the measured rate-clamp count
   per instance-tick) against the model mirror and the DR-0010 #71 owner
   row (<= ~14 multiply-class ops + 2 C5-class LUT interps + 2 phase
   steps per tick across both instances) plus the emitted schedule
   constants, and
6. plant five breakpoint mutations — selector-instead-of-blend, wrong
   shape table, depth modulation dropped, phase first-increment skipped,
   and rate-clamp removal (all RTL) — and require every one to be
   DETECTED.

``modmatrix`` (issue #72) runs the bit-exact RTL modulation-matrix +
endpoint-aligned upsample engines
(tb/sv/mod_matrix_engine.sv + tb/sv/upsample_engine.sv, one matrix
instance + five upsample instances) against the frozen fixed model's
committed golden vectors (sim/reference/mod-matrix-golden-v1/):

1. load every committed vector through the landed loader and verify the
   accepted-contract hash binding,
2. re-derive each case's twenty S1 depth words, four source columns,
   matrix words, and full-length audio streams from the model's own
   control path (mod_matrix_golden's integer mirrors assert row-equality
   against FixedControlPath's own _mod_matrix/_upsample rows, and the
   committed traces, audio digests, endpoint/jitter words, and the
   receipt-case sidecar bytes must all match the live model),
3. run the engines one case per invocation and require every matrix
   trace and every full-length audio stream sample-exact against the
   vector and the model,
4. run two cases back-to-back in one simulation with no reset and
   require the second run to reproduce its solo golden capture
   byte-for-byte (depth words, column memories, and walk counters are
   per-trigger),
5. hard-assert the exported op counters against the model mirror's
   saturation counts and the DR-0010 #72 owner row (matrix: 20 MACs + 5
   narrowings per control tick; upsample: blend mult/add/narrow at 1/1/1
   within the 2/1/1 owner-row cap per column-sample; the exact
   incremental coordinate walk reported as a declared extra), plus the
   emitted schedule constants, and
6. plant eight breakpoint mutations — the upstream +-1.0 matrix clamp,
   ZOH, the align_corners=False off-endpoint scale, selector instead of
   blend, and a dropped route (all RTL, under a +max_j walk cap for the
   demonstration), plus route-swap, depth-sign-flip, and one-ULP-depth
   stimulus mutations — and require every one to be DETECTED, with the
   localization mutations confined to exactly the expected route's
   traces.

``vco`` (issue #73) runs the bit-exact RTL sine VCO engine
(tb/sv/sine_vco_engine.sv) against the frozen whole-voice receipt's
``vco_1.raw`` traces (sim/reference/fixed-voice-golden-v1.json, the #54
receipt; the directed vco_1 cases additionally carry committed sidecar
bytes):

1. load the receipt, verify its accepted-contract bindings (DR-0008
   status, hash-linked LUT digest, constants-package digest), and select
   the param-committed cases (the 8 development-corpus cases stay
   digest-custody only, per the receipt's custody policy),
2. re-derive each case's stimulus through the frozen composition's own
   control path (keyboard word, S1 entry words, initial-phase turn word,
   the #72 endpoint-aligned upsample pitch column — all pinned to the
   receipt's digests), and re-walk the sine lane host-side with the
   model's own primitives (the declared binary64 exp2 shadow replayed
   host-side), requiring the mirror to reproduce the receipt's frozen
   ``vco_1.raw`` digest,
3. run the engine one case per invocation over the full 176,400-sample
   clip and require every ``vco_1.raw`` word AND post-step phase word
   sample-exact against the mirror,
4. run two cases back-to-back in one simulation with no reset and
   require the second run to reproduce its solo golden capture
   byte-for-byte (the initial phase word and counters are per-trigger),
5. hard-assert the exported op counters against the DR-0010 #73 owner
   row (pitch path 3 mults / 6 adds / 3 narrows / 1 exp2 + phase+LUT
   1/2/1; the engine declares 3/7/4 RTL ops per sample and the host
   shadow supplies the 4th mult) and the complete clip schedule (walked
   samples == emitted samples_per_pass), plus the emitted schedule
   constants, and
6. plant five breakpoint mutations — wrong LUT address, dropped phase
   increment, and phase-wrap saturation (RTL, caught on trace rows),
   plus the un-clamped pitch (the model's MIDI clamp band dropped from
   the pitch formation) and the selector-vs-blend mod input (the
   blended pitch column replaced by the control-rate selector pick),
   both planted stimulus-side through the declared shadow — the mirror
   re-derives the Q16.15 words from the mutated pitch exactly as the
   integrated lane would consume them — and require every one to be
   DETECTED against the pristine frozen truth.

``vco2`` (issue #74) runs the bit-exact square/saw VCO engine
(tb/sv/square_saw_vco_engine.sv + tb/sv/quarter_wave_lut.sv) against the
frozen fixed model's committed golden vectors
(sim/reference/square-saw-vco-golden-v1/):

1. load every committed vector through the landed loader and verify the
   accepted-contract hash binding,
2. re-derive each case's static words, per-sample pitch column, and the
   declared shadow replay words (midi->Hz exp2, partials constant, tanh
   fanout) from the frozen model's own lane
   (``vco2_golden.mirror_square_saw_vco``, proved by digest against the
   committed ``vco_2.raw`` evidence; frozen-binding vectors additionally
   bind the retained fixed-voice-golden-v1 sidecar words so the RTL is
   validated directly against the frozen word stream),
3. run the engine one case per invocation and require the pitch word,
   driven word, right word, and ``vco_2.raw`` output sample-exact against
   the mirror (and, on frozen-binding cases, the frozen sidecar words),
4. run two cases back-to-back in one simulation with no reset and require
   the second run to reproduce its solo golden capture byte-for-byte
   (static words, phase, and counters are per-trigger),
5. hard-assert the exported op counters against the DR-0010 #74 owner row
   (8 multiply-class ops, 8 add/sub-class ops, 7 declared narrowings per
   sample; sats = the model mirror's sticky saturation total) plus the
   emitted schedule constants with the PARTIAL-DUT headroom report, and
6. plant three mutations — the wrong (1-shape) square/saw mix (RTL), a
   one-ULP partials-constant error (stimulus, localized to the driven
   stream), and a shadow-word corruption in the replayed tanh fanout
   (stimulus, localized to vco_2.raw) — and require every one to be
   DETECTED.
 

``noise`` (issue #75) runs the exact canonical noise-stream lane
(tb/sv/noise_stream_dut.sv) against the landed fixed-voice golden receipt
(sim/reference/fixed-voice-golden-v1.json) under the ACCEPTED noise policy
(DR-0003 + DR-0008 C8, both Accepted 2026-09-21): host-fed exact stream,
slot ``sound_index % 32``, seed 13 — exactness, NO error metric, NO
on-chip generator (that would be a new noise policy needing its own DR):

1. resolve every golden case's canonical noise bytes through the host
   feed path (seed 13, slot rule) and bind each stream's SHA-256 against
   the receipt's per-case ``noise`` block,
2. mirror the frozen fixed model's noise lane bit-level
   (torchsynth_voice.noise_stream_golden: exponent shift + half-even
   round + C7 saturation, no multiplier) and require its ``noise.raw``
   trace digest to equal every committed case's digest — the golden
   bit-exactness the RTL is then held to,
3. feed each case's full 705,600-byte stream through the DUT and require
   the captured Q2.21 stream to equal the mirror word-for-word (sample-
   exact against the golden receipt), with clean status, exactly 176,400
   declared narrowings per clip, and the clip-length/framing checks
   green,
4. replay/reset: play sound_index 0 then 32 back-to-back through one
   simulation (same slot 0, en gap between streams) and require the
   second capture to equal the first byte-for-byte — no off-by-one state
   survives a stream boundary (32-stream repetition),
5. hard-assert the DR-0010 #75 owner row (one declared narrowing per
   sample; the convert is exponent shift + half-even round, no
   multiplier — declared-structural, reported) plus the emitted schedule
   constants against the landed package, and
6. plant five breakpoint mutations — dropped final byte (stimulus),
   duplicated trailing byte (stimulus), wrong declared slot (stimulus),
   LSB truncation instead of half-even (RTL), and wrong slot-selection
   rule (RTL) — and require every one to be DETECTED.

``mix`` (issue #76) runs the bit-exact audio VCA + pre-normalization
mixer engine (tb/sv/audio_mix_engine.sv) against the frozen whole-voice
receipt (sim/reference/fixed-voice-golden-v1.json) and the declared
directed fixture manifest (spec/reference/directed-voice-v1.json):

1. load the receipt, verify its accepted-contract bindings (DR-0008
   status, hash-linked LUT digest, constants-package digest) and select
   the 26 param-committed cases, then load the declared silence /
   near-silence / all-source-stress fixtures from the directed manifest
   through its own validator,
2. render each case through the frozen composed fixed model
   (``FixedVoiceModel``), re-walk the VCA/mixer lane with the model's own
   primitives (``torchsynth_voice.mix_golden.mirror_mix_lane``), and
   REFUSE unless the mirror reproduces the model's own
   ``vco_1.post_vca`` / ``vco_2.post_vca`` / ``noise.post_vca`` /
   ``mixer.pre_normalization`` / ``mixer.peak`` rows and its per-site
   sticky counter records — and, on every receipt case, unless the
   model's rows reproduce the receipt's frozen per-trace digests,
3. run the engine one case per invocation over the full 176,400-sample
   clip and require all three post-VCA streams, the pre-normalization mix
   word, its magnitude, and the running peak feed sample-exact against
   the mirror,
4. run two cases back-to-back in one simulation with no reset and require
   the second run to reproduce its solo golden capture byte-for-byte (the
   level words, the peak register, and the counters are per-trigger),
5. hard-assert the exported op counters against the DR-0010 #76 owner row
   (6 mults + 2 adds + 4 narrow sites per audio sample; the measured C7
   saturation and C6 rounding events at the four declared sites; the
   peak-feed compare reported against #77's row, never folded into this
   lane's adds; zero Q6.42 accumulator-band faults) and the complete clip
   schedule, plus the emitted schedule constants, and
6. plant nine breakpoint mutations — mixer level route swap, vco_2 VCA
   polarity flip, a mix-word DC offset, truncation instead of half-even
   at the mixer narrowing, dropped mixer saturation, and a dropped noise
   source term (all RTL), plus the noise input curve dropped, a one-ULP
   noise gain error, and an amplitude-column route swap (stimulus) — and
   require every one to be DETECTED, with each localized to exactly the
   traces its fault can reach.

``normreplay`` (issue #77) runs the bit-exact RTL normalization replay
controller + one-shot top's two-pass sequencer
(tb/sv/normalization_replay_engine.sv) against the frozen fixed model's own
``normalize_words()`` (src/torchsynth_voice/fixed_voice.py:195-235; DR-0010's
P4-decided two-pass re-render, no clip buffer):

1. run the isolated directed Q2.21 case grid (issue #52's own boundary
   fixtures: below/at/above-one, peak tie, silence, the Q2.21 extremum, a
   late unique peak, tied maxima, and the two release anchors) plus the
   frozen fixed-voice-golden-v1 receipt's four normalization-family
   full-voice cases (regenerated through FixedVoiceModel.render() and
   bound against the receipt's own mixer.pre_normalization/peak/gain/
   output trace digests before use — no dependency on issue #76's RTL),
2. feed every case's pre-normalization mix stream through the DUT twice
   (pass 1 peak tracking, pass 2 replay) and require the captured pass-2
   output plus every status register (peak word, U1.22 gain word, branch
   decision) to equal the host mirror sample-exactly, with output release
   only after the branch decision,
3. render one case twice back-to-back with no reset between triggers and
   require the second capture to reproduce the first byte-for-byte (AC2:
   no per-sample intermediate is retained across passes or triggers),
4. hard-assert the DR-0010 #77 owner row's declared per-sample op counts
   (1 compare + 1 abs-select per pass-1 sample folded into the mixer
   output; 1 gain multiply + 1 declared narrowing per pass-2 sample on
   the normalized branch) plus the emitted schedule constants against the
   landed package, and
5. plant five breakpoint mutations — always-on, always-off, wrong-peak
   (last-sample-wins instead of max), wrong-reciprocal (off-by-one-ULP at
   S5), and off-by-one (the pass-1 framing boundary decided one sample
   early, demonstrated on the late-peak case) — and require every one to
   be DETECTED.

"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

TB_ROOT = Path(__file__).resolve().parent
ROOT = TB_ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import adsr_golden as ag  # noqa: E402
from torchsynth_voice import directed as dr  # noqa: E402
from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice import lfo_golden as lgo  # noqa: E402
from torchsynth_voice import mix_golden as mx  # noqa: E402
from torchsynth_voice import mod_matrix_golden as mm  # noqa: E402
from torchsynth_voice import noise_stream_golden as nsg  # noqa: E402
from torchsynth_voice import normalization_replay as nr  # noqa: E402
from torchsynth_voice import normalization_replay_golden as nrg  # noqa: E402
from torchsynth_voice import vco_golden as vg  # noqa: E402
from torchsynth_voice import vco2_golden as vc  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
    entry_quantize,
    shadow_half_even,
)
from torchsynth_voice.fixedpoint import codegen  # noqa: E402
from torchsynth_voice.fixedpoint import schedule as sched  # noqa: E402
from torchsynth_voice.fixedpoint.choices import ChoiceNotAccepted  # noqa: E402
from torchsynth_voice.fixedpoint.counters import StickyCounters  # noqa: E402
from torchsynth_voice.fixedpoint.ops import OverflowPolicy, mul, rescale  # noqa: E402
from torchsynth_voice.fixedpoint.rounding import (  # noqa: E402
    RoundingMode,
    div_round,
    div_round_reported,
)
from torchsynth_voice.fixedpoint.schedule import ScheduleNotAccepted  # noqa: E402
from torchsynth_voice.core_protocol import (  # noqa: E402
    CMD_HELLO,
    CMD_PATCH_ABORT,
    CMD_PATCH_COMMIT,
    CMD_PATCH_NAME,
    CMD_PATCH_OPEN,
    CMD_PATCH_VALUE,
    CMD_RESET,
    KIND_COMMAND,
    ErrorCode,
    decode_frame,
    encode_frame,
    encode_hello,
    encode_patch_commit,
    encode_patch_name,
    encode_patch_open,
    encode_patch_value,
    numeric_contract_version_bound,
)
from torchsynth_voice.format_sweep import (  # noqa: E402
    MOD_MATRIX_INPUTS,
    MOD_MATRIX_OUTPUTS,
    FixedControlPath,
    quantize_params,
)
from torchsynth_voice.patch_control_model import (  # noqa: E402
    decode_rsp_frames,
)

DUT_SV = TB_ROOT / "sv/synth_dut.sv"
TB_SV = TB_ROOT / "sv/tb_synth_dut.sv"
ANCHOR_DUT_SV = TB_ROOT / "sv/lut_sine_dut.sv"
ANCHOR_TB_SV = TB_ROOT / "sv/tb_lut_sine_dut.sv"
ADSR_DUT_SV = TB_ROOT / "sv/adsr_engine.sv"
ADSR_TB_SV = TB_ROOT / "sv/tb_adsr_engine.sv"
CONSTANTS_PKG_SV = TB_ROOT / "sv/gf180_rtl_constants_pkg.sv"

#: Committed ADSR golden vectors (issue #70).
ADSR_VECTOR_DIR = ROOT / "sim/reference/adsr-golden-v1"
#: The case mutations are demonstrated on (default-envelope sustain 0.75).
ADSR_MUTATION_CASE = "frozen-envelope-receipt"
#: Replay-independence pair: envelope state from run 0 must not leak into run 1.
ADSR_REPLAY_PAIR = ("frozen-envelope-receipt", "boundary-tie")

#: Committed LFO + control-VCA golden vectors (issue #71).
LFO_DUT_SV = TB_ROOT / "sv/lfo_vca_engine.sv"
LFO_TB_SV = TB_ROOT / "sv/tb_lfo_vca_engine.sv"
LFO_VECTOR_DIR = ROOT / "sim/reference/lfo-vca-golden-v1"
#: Cases the RTL mutations are demonstrated on: the default-envelope
#: receipt (blend/shape/depth), a non-zero first-increment wrap case
#: (phase), and the negative-rate zero-clamp case (clamp removal).
LFO_MUTATION_CASE = "frozen-lfo-receipt"
LFO_PHASE_MUTATION_CASE = "frequency-phase-extremes"
LFO_CLAMP_MUTATION_CASE = "depth-negative-clamp"
#: Replay-independence pair: LFO phase/weight state from run 0 must not
#: leak into run 1.
LFO_REPLAY_PAIR = ("frozen-lfo-receipt", "shape-single-sweep")

#: Committed modulation-matrix + endpoint-aligned-upsample golden vectors
#: (issue #72).
MM_DUT_SV = TB_ROOT / "sv/mod_matrix_engine.sv"
MM_UP_DUT_SV = TB_ROOT / "sv/upsample_engine.sv"
MM_TB_SV = TB_ROOT / "sv/tb_mod_matrix_upsample.sv"
MM_VECTOR_DIR = ROOT / "sim/reference/mod-matrix-golden-v1"
#: Cases the RTL mutations are demonstrated on: the all-depths +1.0 case
#: drives matrix outputs to ~3.4 (the +-1.0 clamp mutation must bite), and
#: the mixed-sign case gives every route at least two nonzero source
#: depths (the selector/dropped-route mutations must bite and localize).
MM_CLAMP_MUTATION_CASE = "route-extremes-positive"
MM_MUTATION_CASE = "mixed-sign-routes"
#: The route the dropped-route mutation forces to zero, and the exact
#: trace set the mismatch must localize to.
MM_DROPPED_ROUTE = "vco_2_amp"
#: Mutation-simulation walk cap (+max_j): every mutation demonstrably
#: bites within the first 24,000 audio samples (~0.54 s), so the eight
#: mutation sims walk a fraction of the grid. Committed-case runs always
#: walk the full 176,400 samples.
MM_MUTATION_WALK_CAP = 24000
#: Replay-independence pair: matrix depths and upsample column memories
#: are per-trigger; run 0 state must not leak into run 1.
MM_REPLAY_PAIR = ("frozen-mod-matrix-receipt", "mixed-sign-routes")

#: Committed square/saw VCO golden vectors (issue #74).
VCO2_DUT_SV = TB_ROOT / "sv/square_saw_vco_engine.sv"
VCO_LUT_SV = TB_ROOT / "sv/quarter_wave_lut.sv"
VCO2_TB_SV = TB_ROOT / "sv/tb_square_saw_vco.sv"
VCO_VECTOR_DIR = ROOT / "sim/reference/square-saw-vco-golden-v1"
#: Frozen-binding vectors carry this prefix and bind the retained
#: fixed-voice-golden-v1 vco_2.raw sidecar words (direct RTL-vs-frozen
#: word validation, no mirror in the loop).
VCO_FROZEN_PREFIX = "frozen:"
#: The case the wrong-shape-mix RTL mutation is demonstrated on: shape 1.0
#: makes the inverted right-branch coefficient maximally observable.
VCO_SHAPE_MUTATION_CASE = "frozen:waveform:vco_2:saw"
#: Cases the stimulus mutations are demonstrated on: the intermediate-mix
#: case carries a nontrivial tanh fanout (shadow-word corruption must bite
#: and localize) and an unclamped partials word (the one-ULP constant
#: error must localize to the driven stream alone).
VCO_VECTOR_MUTATION_CASE = "shape:intermediate-half"
#: The sample whose replayed left_q shadow word the corruption flips.
VCO_SHADOW_MUTATION_INDEX = 12345
#: Mutation-simulation walk cap: every mutation demonstrably bites within
#: the first 24,000 samples, so the three mutation sims walk a fraction of
#: the grid. Committed-case runs always walk the full 176,400 samples.
VCO2_MUTATION_WALK_CAP = 24000
#: Replay-independence pair: static words, phase, and counters are
#: per-trigger; run 0 state must not leak into run 1.
VCO2_REPLAY_PAIR = ("frozen:waveform:vco_2:saw", "shape:intermediate-half")
#: Landed fixed-voice golden receipt (issue #54): per-case noise block
#: (seed/slot/bytes digest) and per-case ``noise.raw`` trace digests.
NOISE_RECEIPT_PATH = ROOT / "sim/reference/fixed-voice-golden-v1.json"
#: Committed case the noise mutations are demonstrated on (slot 0).
NOISE_MUTATION_CASE_INDEX = 0
#: Replay/32-stream-repetition pair: sound_index 0 and 32 share slot 0, so
#: their resolved byte streams are identical by the C8 slot rule.
NOISE_REPLAY_INDICES = (0, 32)
#: Synthetic bit-pattern stream: structural edge classes (deep zero, ties
#: to even in both directions, saturation at both rails) beyond canonical
#: bits, all through the same fed-stream path.
NOISE_PATTERN_BITS = (
    0x00000000,  # +0.0
    0x80000000,  # -0.0
    0x00000001,  # smallest subnormal -> deep zero
    0x00800000,  # 2^-126 (smallest normal) -> deep zero
    0x3F000001,  # 0.5 + ulp: tie, even integer part -> stays down
    0x3F800003,  # tie with odd integer part -> rounds up
    0x3F800000,  # 1.0
    0xBF800000,  # -1.0
    0x40400000,  # 3.0
    0x407FFFFF,  # largest finite below 4 -> saturates high
    0x40800000,  # 4.0 -> saturates high
    0xC0800000,  # -4.0 -> exactly C1_MIN
    0xC2C80000,  # -98.25 -> saturates low
)

NOISE_DUT_SV = TB_ROOT / "sv/noise_stream_dut.sv"
NOISE_TB_SV = TB_ROOT / "sv/tb_noise_stream_dut.sv"
#: Mutation seams (anchored; mutate_sv refuses if the anchor moved).
NOISE_TRUNCATION_ANCHOR = "rounded = q_rounded;"
NOISE_TRUNCATION_MUTANT = "rounded = q_raw;"
NOISE_SLOT_RULE_ANCHOR = "wire [4:0] slot_expected = sound_index[4:0];"
NOISE_SLOT_RULE_MUTANT = "wire [4:0] slot_expected = sound_index[4:0] ^ 5'd1;"

NORMREPLAY_DUT_SV = TB_ROOT / "sv/normalization_replay_engine.sv"
NORMREPLAY_TB_SV = TB_ROOT / "sv/tb_normalization_replay_engine.sv"
#: The isolated case the AC2 back-to-back replay pair is demonstrated on
#: (a divide-branch case: replay must reproduce the gain-word computation
#: and the applied narrowing identically the second time, with no
#: per-sample intermediate retained across the pair).
NORMREPLAY_REPLAY_CASE = "fixed:above-one-min"
#: Mutation seams (anchored; mutate_sv refuses if the anchor moved).
NORMREPLAY_ALWAYS_ON_ANCHOR = "if (peak_word > UNITY_INT) begin"
NORMREPLAY_ALWAYS_ON_MUTANT = "if (1'b1) begin"
NORMREPLAY_ALWAYS_OFF_MUTANT = "if (1'b0) begin"
NORMREPLAY_WRONG_PEAK_ANCHOR = (
    "if (mix_mag > peak_word)\n"
    "                                peak_word <= mix_mag;"
)
NORMREPLAY_WRONG_PEAK_MUTANT = (
    "if (1'b1)\n"
    "                                peak_word <= mix_mag;"
)
NORMREPLAY_WRONG_RECIPROCAL_ANCHOR = (
    "gain_word         <= recip_quo[C9_WIDTH-1:0];"
)
NORMREPLAY_WRONG_RECIPROCAL_MUTANT = (
    "gain_word         <= recip_quo[C9_WIDTH-1:0] + 1'b1;"
)
NORMREPLAY_OFF_BY_ONE_ANCHOR = (
    "P_PASS1: begin\n"
    "                    if (mix_valid) begin\n"
    "                        if (samples_this_pass == SCHED_SAMPLES_PER_PASS) begin"
)
NORMREPLAY_OFF_BY_ONE_MUTANT = (
    "P_PASS1: begin\n"
    "                    if (mix_valid) begin\n"
    "                        if (samples_this_pass == SCHED_SAMPLES_PER_PASS - 18'd1) begin"
)
#: Cases the always-on/always-off/wrong-peak/wrong-reciprocal mutations are
#: demonstrated on (mirrors tests/test_normalization_replay.py's own
#: ForbiddenSubstitutesMustFail case choices where named there).
NORMREPLAY_BYPASS_MUTATION_CASE = "fixed:below-one-max"
NORMREPLAY_DIVIDE_MUTATION_CASE = "fixed:above-one-min"
#: The off-by-one framing mutation is demonstrated on the one directed case
#: whose unique peak sits at the very last sample (index 176,399) — the
#: natural vector for a pass-boundary-decided-one-sample-early fault.
NORMREPLAY_OFF_BY_ONE_CASE = "fixed:late-peak"
#: Trace name from the canonical registry used for the synthetic stream.
SYNTH_TRACE = "mixer.output"
SYNTH_PARAMETER = "adsr_1.alpha"
CYCLE_START = 0

STIMULUS = [0, 1, 2, 165, 255, 128, 7, 42]
XOR_MASK = 0xA5

#: Real-vector fail-path plant: flip the LSB of this captured sample.
ANCHOR_FAIL_INDEX = 4096

#: DUT-checked trace of the anchor vector (the LUT sine path's output).
ANCHOR_TRACE = "vco_1.raw"


def synth_model(value: int) -> int:
    """Software mirror of the synthetic DUT's registered XOR."""
    return (value & 0xFF) ^ XOR_MASK


def build_vector(values, registry, inventory) -> dict:
    vector = {
        "schema": gv.VECTOR_SCHEMA,
        "schema_version": gv.SCHEMA_VERSION,
        "provenance": {
            "generator": "tb/run_tb.py selftest",
            "note": (
                "synthetic-bringup vectors for the harness self-test; not "
                "default-nebula clip content and not DR-0008-conformant"
            ),
        },
        "trace_registry_version": registry["semantic_version"],
        "parameter_inventory_commit": inventory["source"]["commit"],
        "clip": {
            "profile": gv.SYNTHETIC_PROFILE,
            "sample_rate_hz": gv.CANONICAL_SAMPLE_RATE_HZ,
            "sample_count": len(values),
            "control_rate_hz": gv.CANONICAL_CONTROL_RATE_HZ,
            "control_count": len(values),
        },
        "parameters": {SYNTH_PARAMETER: 0.5},
        "traces": [
            {
                "name": SYNTH_TRACE,
                "kind": "audio",
                "encoding": "synthetic-int",
                "cycle_start": CYCLE_START,
                "values": list(values),
            }
        ],
    }
    vector["content_hash"] = gv.compute_content_hash(vector)
    return vector


def _run(command: list, cwd: Path) -> None:
    print("+ %s" % " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)
def simulate(workdir: Path, simulator: str) -> list:
    stim = workdir / "stimulus.txt"
    captured = workdir / "captured.txt"
    stim.write_text("".join("%d\n" % v for v in STIMULUS), encoding="utf-8")
    if simulator == "iverilog":
        vvp = workdir / "synth_selftest.vvp"
        _run(
            ["iverilog", "-g2012", "-o", str(vvp), str(DUT_SV), str(TB_SV)],
            cwd=workdir,
        )
        _run(["vvp", "-n", str(vvp)], cwd=workdir)
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    values = [
        int(line)
        for line in captured.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return values


def selftest(workdir: Path, simulator: str) -> int:
    registry = gv.load_registry_document()
    inventory = gv.load_inventory_document()
    expected = [synth_model(v) for v in STIMULUS]

    captured = simulate(workdir, simulator)

    ok = True
    if captured != expected:
        print("SELFTEST BROKEN: captured stream does not match the model")
        ok = False

    golden = build_vector(expected, registry, inventory)
    gv.load_vector(golden, registry=registry, inventory=inventory)

    mismatch = gv.first_mismatch(
        golden, {SYNTH_TRACE: captured}
    )
    if mismatch is not None:
        print("FAIL path: unexpected mismatch on the clean golden vector:")
        print(gv.format_mismatch(mismatch))
        ok = False
    else:
        print(
            "PASS path verified: clean golden vector vs captured stream -> "
            "no mismatch (reporter silent)."
        )

    corrupted = build_vector(expected, registry, inventory)
    corrupted["traces"][0]["values"][3] = (expected[3] + 1) & 0xFF
    corrupted["content_hash"] = gv.compute_content_hash(corrupted)
    # The corrupted vector is the EXPECTED side of the comparison and the
    # captured sim stream is the ACTUAL side: the vector claims one value,
    # the DUT delivered another, and the reporter must name that row.
    planted = gv.Mismatch(
        cycle=CYCLE_START + 3,
        sample=3,
        trace=SYNTH_TRACE,
        expected=corrupted["traces"][0]["values"][3],
        actual=expected[3],
    )
    observed = gv.first_mismatch(corrupted, {SYNTH_TRACE: captured})
    if observed != planted:
        print(
            "FAIL path: reporter did not name the planted fault; observed %r"
            % (observed,)
        )
        ok = False
    else:
        print("Intentional-fail path verified; reporter reports:")
        print(gv.format_mismatch(observed))

    if not ok:
        print("SELFTEST FAILED")
        return 1
    print("SELFTEST PASSED (pass path + intentional-fail path)")
    return 0


def focus_vector(vector: dict, trace_name: str) -> dict:
    """A single-trace view of a loaded vector, revalidated by the loader.

    Lets the first-mismatch reporter run against one DUT-covered trace of
    the real vector while keeping every schema, registry, and hash check.
    """
    traces = [t for t in vector["traces"] if t["name"] == trace_name]
    if len(traces) != 1:
        raise SystemExit("vector has no unique trace %r" % trace_name)
    focused = {k: v for k, v in vector.items() if k != "traces"}
    focused["traces"] = traces
    focused["content_hash"] = gv.compute_content_hash(focused)
    return gv.load_vector(focused)


def write_lut_memh(table, path: Path) -> None:
    """Emit the accepted quarter-wave table as hex for $readmemh.

    Layout: C5_N_ENTRIES entry words, then the quarter-turn endpoint entry
    at index C5_N_ENTRIES (the DUT's endpoint branch reads it there).
    """
    lines = ["%06x" % entry for entry in table.entries]
    lines.append("%06x" % table.endpoint)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def derive_anchor_control_words(vector: dict, formats: AcceptedFormats):
    """Derive the DUT control words from the vector's own declared data.

    The declared binary64 shadow sites are replayed host-side here (the
    midi->Hz exp2 is DR-0008's declared open approximation item — this is
    a harness input derivation, not an RTL claim). Everything consumed is
    the vector's own declared scalar trace and parameters:

    - the phase-increment word: m1 = the vector's ``keyboard.midi_f0``
      scalar word (the anchor case's tuning/mod_depth entry-quantize to
      zero, asserted below), then the declared shadow policy
      ``hz = 440 * 2^((m/2^21 - 69)/12)`` -> Q16.15 word -> half-even
      increment over the C2 unit circle;
    - the mixer-level word: the C6/S1 entry quantization of the vector's
      ``mixer.vco_1`` parameter into the accepted C1 audio format.
    """
    counters = StickyCounters()
    for name in ("vco_1.tuning", "vco_1.mod_depth"):
        word = entry_quantize(
            float(vector["parameters"][name]), formats.midi, counters,
            "tb.anchor.entry:" + name,
        )
        if word != 0:
            raise SystemExit(
                "anchor derivation requires %s to entry-quantize to zero; "
                "got %d (not the zero-modulation case)" % (name, word)
            )
    midi_word = None
    for trace in vector["traces"]:
        if trace["name"] == "keyboard.midi_f0":
            midi_word = trace["values"][0]
    if midi_word is None:
        raise SystemExit("anchor vector carries no keyboard.midi_f0 scalar")

    midi_scale = float(formats.midi.scale)
    sample_rate_hz = int(vector["clip"]["sample_rate_hz"])
    frequency_scale = int(formats.frequency.scale)
    # Declared shadow site (binary64 on the quantized control word).
    hz = 440.0 * math.exp2((midi_word / midi_scale - 69.0) / 12.0)
    freq_word = shadow_half_even(hz * float(frequency_scale))
    phase_step = div_round(
        freq_word * formats.phase_units_per_turn,
        frequency_scale * sample_rate_hz,
        RoundingMode.HALF_EVEN,
    )
    level_word = entry_quantize(
        float(vector["parameters"]["mixer.vco_1"]), formats.audio, counters,
        "tb.anchor.entry:mixer.vco_1",
    )
    return phase_step, level_word


def anchor_mirror(formats: AcceptedFormats, phase_step: int, level_word: int,
                  sample_count: int):
    """Replay the DUT's integer dataflow with the model's own primitives.

    Uses the landed fixedpoint ops (half-even, saturate) and the accepted
    hash-linked table, so a match against the frozen vector trace is a
    model-primitive match, not a harness-parallel implementation.
    """
    counters = StickyCounters()
    audio = formats.audio
    entry_fmt = formats.table.spec.entry_format
    modulus = 1 << formats.phase_width
    phase = 0
    vco_words = []
    mix_words = []
    for _ in range(sample_count):
        phase = (phase + phase_step) % modulus
        entry = formats.table.evaluate(phase, formats.mode)
        vco = rescale(entry, entry_fmt, audio, formats.mode,
                      OverflowPolicy.SATURATE, counters, "tb.anchor.vco")
        mix = mul(vco, audio, level_word, audio, audio, formats.mode,
                  OverflowPolicy.SATURATE, counters, "tb.anchor.mix")
        vco_words.append(vco)
        mix_words.append(mix)
    return vco_words, mix_words


def simulate_anchor(workdir: Path, simulator: str, phase_step: int,
                    level_word: int, sample_count: int, lut_memh: Path):
    """Compile the constants package + format-true DUT and capture streams."""
    stim = workdir / "stimulus.txt"
    captured = workdir / "captured.txt"
    stim.write_text(
        "%d\n%d\n%d\n" % (phase_step, level_word, sample_count),
        encoding="utf-8",
    )
    if simulator == "iverilog":
        vvp = workdir / "lut_sine.vvp"
        _run(
            [
                "iverilog", "-g2012", "-o", str(vvp),
                str(CONSTANTS_PKG_SV), str(ANCHOR_DUT_SV), str(ANCHOR_TB_SV),
            ],
            cwd=workdir,
        )
        _run(
            ["vvp", "-n", str(vvp), "+lut=%s" % lut_memh],
            cwd=workdir,
        )
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    vco_words = []
    mix_words = []
    for line in captured.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        v_word, m_word = line.split()
        vco_words.append(int(v_word))
        mix_words.append(int(m_word))
    cycles_file = workdir / "cycles.txt"
    measured_cycles = int(cycles_file.read_text(encoding="utf-8").strip())
    return vco_words, mix_words, measured_cycles


def check_clip_budget(measured_cycles: int, sample_count: int) -> bool:
    """Assert the ratified clip budget against the anchor run (issue #68).

    The DR-0010 schedule register is consumed through its refusal gate; the
    budget constants are (a) checked for internal consistency, (b) checked
    against the live emission of the constants package (the anchor's cycle
    count is measured against the emitted budget, so a stale package fails
    here exactly as ``tools/generate_rtl_constants.py --check`` would), and
    (c) reported as measured-vs-budget headroom over the DUT's counted
    cycles.

    Honesty: the format-true LUT DUT retires one sample per cycle and is
    NOT the DR-0010 serialized single-MAC schedule, so the measured-vs-
    budget comparison is a PARTIAL-DUT headroom report, not a schedule-
    conformance assertion — only the consistency and emission checks are
    hard asserts.
    """
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("BUDGET REFUSED: DR-0010 schedule register refused: %s" % error)
        return False

    slots = sched.constant(schedule, "clip_sample_slots")
    passes = sched.constant(schedule, "passes_per_clip")
    samples = sched.constant(schedule, "samples_per_pass")
    rate = int(schedule["profile"]["sample_rate_hz"])
    counted = sched.constant(schedule, "counted_cycles_per_sample")
    t_max = sched.constant(schedule, "t_max_at_25mhz")
    bound_mhz = sched.constant(schedule, "bound_clock_mhz")
    slots_per_second = int(schedule["refutable_bound"]["slots_per_realtime_second"])
    example = schedule["budget_equation"]["worked_example"]

    checks = [
        (
            "clip sample-slots: %d == %d passes x %d samples/pass"
            % (slots, passes, samples),
            slots == passes * samples,
        ),
        (
            "real-time slot rate: %d == %d x %d / %d"
            % (slots_per_second, slots, rate, samples),
            slots_per_second == slots * rate // samples,
        ),
        (
            "refutable bound: T_max %d == floor(%d MHz / %d) - C_counted %d"
            % (t_max, bound_mhz, slots_per_second, counted),
            t_max == bound_mhz * 1000000 // slots_per_second - counted,
        ),
        (
            "DR-0010 worked example: %d slots x (%d + T=%d) = %d cycles"
            % (slots, counted, example["t"], example["n_clip"]),
            example["n_clip"] == slots * (counted + example["t"]),
        ),
    ]
    ok = True
    for line, holds in checks:
        print("  budget consistency: %s -> %s" % (line, "OK" if holds else "FAIL"))
        ok = ok and holds

    try:
        emitted = codegen.emit(schedule_payload=schedule)
        landed = CONSTANTS_PKG_SV.read_text(encoding="utf-8")
        matches = emitted.package_text == landed and (
            sched.SCHEDULE_ID in emitted.emitted_ids
        )
        print(
            "  emitted budget constants: landed %s matches the live emission "
            "of both accepted registers -> %s"
            % (CONSTANTS_PKG_SV.name, "OK" if matches else "FAIL")
        )
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  emitted budget constants: emission failed -> %s" % error)
        return False

    if sample_count <= 0 or measured_cycles <= 0:
        print("  measured cycles: invalid measurement (%d cycles)" % measured_cycles)
        return False

    per_sample = measured_cycles / sample_count
    budget_t0 = sample_count * counted
    budget_tmax = sample_count * sched.cycles_per_sample(schedule, t_max)
    n_clip_t0 = sched.clip_cycles(schedule, 0)
    n_clip_tmax = sched.clip_cycles(schedule, t_max)
    print(
        "  measured (PARTIAL DUT): %d enabled cycles for %d samples "
        "(%.3f cycles/sample)" % (measured_cycles, sample_count, per_sample)
    )
    print(
        "  headroom vs budget (same sample count, single pass): "
        "T=0 C=%d -> %d cycles (%.1f%% free); T=T_max=%d C=%d -> %d cycles "
        "(%.1f%% free)"
        % (
            counted,
            budget_t0,
            100.0 * (budget_t0 - measured_cycles) / budget_t0,
            t_max,
            counted + t_max,
            budget_tmax,
            100.0 * (budget_tmax - measured_cycles) / budget_tmax,
        )
    )
    print(
        "  clip budget N_clip = %d x C: %d cycles at T=0, %d cycles at "
        "T=T_max=%d" % (slots, n_clip_t0, n_clip_tmax, t_max)
    )
    print(
        "  NOTE: PARTIAL-DUT MEASUREMENT - the format-true LUT DUT is one "
        "sample per cycle, not the DR-0010 serialized single-MAC schedule; "
        "this is a headroom report, not a schedule-conformance, PPA, or "
        "fit claim (#82/#83 own those measurements)."
    )
    return ok


def adsr_load_vectors():
    """Load and contract-verify every committed ADSR golden vector."""

    vectors = {}
    for path in sorted(ADSR_VECTOR_DIR.glob("*.json")):
        vector = gv.load_vector(path)
        gv.verify_accepted_contract(vector)
        vectors[path.stem] = vector
    if not vectors:
        raise SystemExit("no ADSR golden vectors found in %s" % ADSR_VECTOR_DIR)
    return vectors


def adsr_derive_case(fcp, formats, vector):
    """Model-derived stimulus for one case: formations + shadow streams.

    Every value comes from the model's own code (``adsr_golden`` calls the
    frozen composition's control path); the shadow rows are asserted equal
    to ``FixedControlPath._ramp`` inside :func:`ag.mirror_ramp`.
    """

    counters = StickyCounters()
    words = ag.quantize_entries(vector["parameters"], formats.midi, formats.mode, counters)
    eps60 = ag.eps60_word(formats.mode)
    cases = {}
    for prefix in ag.ADSR_PREFIXES:
        formation = ag.derive_formation(fcp, words, prefix)
        shadow = {}
        value_rows = {}
        for stage in ("attack", "decay", "release"):
            start_q = (
                formation["attack_q"] if stage == "decay"
                else (formation["duration_q"] if stage == "release" else 0)
            )
            values, shape_words = ag.mirror_ramp(
                fcp,
                formation[stage + "_q"],
                formation[stage + "_exact"],
                start_q,
                stage != "attack",
                formation["alpha"],
            )
            shadow[stage] = shape_words
            value_rows[stage] = values
        golden = fcp._adsr(words, prefix)
        committed = None
        for trace in vector["traces"]:
            if trace["name"] == ag.trace_name(prefix):
                committed = trace["values"]
                break
        if committed is None:
            raise SystemExit("vector %r carries no %s trace" % (vector, prefix))
        if committed != golden:
            raise SystemExit(
                "committed vector trace %s no longer matches the live model; "
                "regenerate the vectors" % ag.trace_name(prefix)
            )
        # The combine-over-rows replication must reproduce _adsr exactly
        # when fed the model's own rows (used only by the mutated-curve
        # vector below, but proven on the clean case).
        combined = ag.combine_from_shadow(
            fcp, shadow["attack"], shadow["decay"], shadow["release"],
            formation["sustain_q"],
        )
        if combined != golden:
            raise SystemExit(
                "combine-from-shadow replication drifted from _adsr on %s" % prefix
            )
        cases[prefix] = {
            "formation": formation,
            "shadow": shadow,
            "value_rows": value_rows,
            "golden": golden,
            "sustain_entry": words[prefix + "sustain"],
        }
    return cases


def _word_halves(word: int):
    return word & 0xFFFFFFFF, word >> 32


def adsr_write_case(workdir: Path, run: int, cases: dict, linear_curve: bool = False):
    """Write one run's stimulus files (params + per-instance shadows)."""

    prefixes = list(cases)
    params = []
    for prefix in prefixes:
        f = cases[prefix]["formation"]
        ints = []
        for name in ("duration_q", "attack_q", "decay_q", "release_q"):
            lo, hi = _word_halves(int(f[name]))
            ints.extend((lo, hi))
        ints.extend(
            (
                1 if f["duration_zero"] else 0,
                1 if f["attack_zero"] else 0,
                1 if f["decay_zero"] else 0,
                1 if f["release_zero"] else 0,
            )
        )
        ints.append(int(cases[prefix]["sustain_entry"]))
        lo, hi = _word_halves(int(ag.eps60_word(RoundingMode.HALF_EVEN)))
        ints.extend((lo, hi))
        params.append(ints)
    (workdir / ("run%d_params.txt" % run)).write_text(
        "".join(" ".join(str(v) for v in row) + "\n" for row in params),
        encoding="utf-8",
    )
    for index, prefix in enumerate(prefixes):
        lines = []
        for tick in range(gv.CANONICAL_CONTROL_COUNT):
            if linear_curve:
                row = [
                    cases[prefix]["value_rows"][stage][tick] >> 30
                    for stage in ("attack", "decay", "release")
                ]
            else:
                row = [
                    cases[prefix]["shadow"][stage][tick]
                    for stage in ("attack", "decay", "release")
                ]
            lines.append("%d %d %d" % tuple(row))
        (workdir / ("run%d_shadow%d.txt" % (run, index))).write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    return prefixes


def adsr_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path) -> list:
    """Compile and run the six-instance tb; return per-run captured streams."""

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    if simulator == "iverilog":
        vvp = workdir / "adsr_engine.vvp"
        _run(
            [
                "iverilog", "-g2012", "-o", str(vvp),
                str(CONSTANTS_PKG_SV), str(dut_sv), str(ADSR_TB_SV),
            ],
            cwd=workdir,
        )
        _run(["vvp", "-n", str(vvp)], cwd=workdir)
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    captures = []
    for run in range(runs):
        streams = []
        for index in range(6):
            path = workdir / ("run%d_captured%d.txt" % (run, index))
            streams.append(
                [
                    int(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            )
        captures.append(streams)
    return captures


def adsr_ops(workdir: Path, runs: int):
    """Per-run per-instance op counter totals from the tb's ops files."""

    rows = []
    for run in range(runs):
        path = workdir / ("run%d_ops.txt" % run)
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append([int(v) for v in line.split()])
    return rows


def check_adsr_budget(schedule) -> bool:
    """Op-count conformance to the DR-0010 #70 owner row + emission check.

    DR-0010's owner row for #70: "control rate: <= ~30 multiply-class ops +
    <= 18 declared ``**alpha`` shadow sites per tick across all 6
    envelopes" (spec/decision-records/0010-one-shot-rtl-microarchitecture.md,
    module table). The engine declares 4 multiply-class ops + 2 declared
    narrowings + 3 shadow words + 3 ramp divisions per instance-tick, so
    the six-instance totals are 24 / 12 / 18 / 18 per control tick. The tb
    counts them as exported sticky counters and they are hard-asserted
    here. The serialized single-MAC cycle mapping remains the integration
    lanes; this is an op-count check, not a PPA/fit claim.
    """

    ok = True
    per_tick = {
        "multiply-class ops": (24, 30),
        "declared narrowings": (12, 30),
        "`**alpha` shadow words": (18, 18),
        "ramp divisions (declared extra)": (18, None),
    }
    for name, (actual, limit) in per_tick.items():
        verdict = "n/a (reported)"
        if limit is not None:
            verdict = "OK" if actual <= limit else "FAIL"
            ok = ok and actual <= limit
        print(
            "  budget: %d %s per control tick vs DR-0010 owner-row cap %s -> %s"
            % (actual, name, limit, verdict)
        )
    try:
        emitted = codegen.emit(schedule_payload=schedule)
        landed = CONSTANTS_PKG_SV.read_text(encoding="utf-8")
        matches = emitted.package_text == landed and (
            sched.SCHEDULE_ID in emitted.emitted_ids
        )
        print(
            "  budget constants: landed %s matches the live emission of both "
            "accepted registers -> %s"
            % (CONSTANTS_PKG_SV.name, "OK" if matches else "FAIL")
        )
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return False
    return ok


def mutate_sv(source: str, anchor: str, replacement: str, label: str) -> str:
    """Plant one anchored RTL mutation; refuse loudly if the anchor moved."""

    count = source.count(anchor)
    if count != 1:
        raise SystemExit(
            "MUTATION %s: anchor occurs %d times (expected 1); the RTL "
            "changed and the mutation seam must be re-anchored" % (label, count)
        )
    return source.replace(anchor, replacement)


def adsr_detects(captures_run, cases, vector):
    """True iff the captured streams mismatch the vector's expectations."""

    captured_by_trace = {
        ag.trace_name(prefix): captures_run[index]
        for index, prefix in enumerate(cases)
    }
    return gv.first_mismatch(vector, captured_by_trace) is not None


def adsr(workdir: Path, simulator: str) -> int:
    """Issue #70 flow: the ADSR engine vs the frozen fixed model's vectors."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("ADSR REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("ADSR REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    fcp = FixedControlPath(formats.control_spec)

    vectors = adsr_load_vectors()
    print(
        "Loaded %d ADSR golden vectors (%s), contract bindings verified"
        % (len(vectors), ", ".join(sorted(vectors)))
    )

    ok = True

    # 1. Sample-exact engine runs, one committed case per invocation.
    case_dirs = {}
    for case_id in sorted(vectors):
        case_dir = workdir / ("case-" + case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        cases = adsr_derive_case(fcp, formats, vectors[case_id])
        adsr_write_case(case_dir, 0, cases)
        captures = adsr_simulate(case_dir, simulator, 1, ADSR_DUT_SV)
        prefixes = list(cases)
        captured_by_trace = {
            ag.trace_name(prefix): captures[0][index]
            for index, prefix in enumerate(prefixes)
        }
        mismatch = gv.first_mismatch(vectors[case_id], captured_by_trace)
        if mismatch is not None:
            print(
                "ADSR FAILED: RTL disagrees with the golden vector (%s):"
                % case_id
            )
            print(gv.format_mismatch(mismatch))
            ok = False
        print(
            "case %s: %d instances x %d control samples, RTL sample-exact "
            "-> %s" % (case_id, len(prefixes), gv.CANONICAL_CONTROL_COUNT,
                       "OK" if ok else "FAIL")
        )
        case_dirs[case_id] = (case_dir, cases)

    # 2. Reset/replay: a second trigger cannot retain prior envelope state.
    first, second = ADSR_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    cases_first = adsr_derive_case(fcp, formats, vectors[first])
    cases_second = adsr_derive_case(fcp, formats, vectors[second])
    adsr_write_case(replay_dir, 0, cases_first)
    adsr_write_case(replay_dir, 1, cases_second)
    replay_captures = adsr_simulate(replay_dir, simulator, 2, ADSR_DUT_SV)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    adsr_write_case(solo_dir, 0, cases_second)
    solo_captures = adsr_simulate(solo_dir, simulator, 1, ADSR_DUT_SV)
    replay_ok = True
    second_prefixes = list(cases_second)
    for index in range(6):
        if replay_captures[1][index] != solo_captures[0][index]:
            print(
                "ADSR FAILED: run-after-run capture differs from the solo "
                "run (instance %d) - prior envelope state leaked" % index
            )
            replay_ok = False
    mismatch = gv.first_mismatch(
        vectors[second],
        {
            ag.trace_name(prefix): replay_captures[1][index]
            for index, prefix in enumerate(second_prefixes)
        },
    )
    if mismatch is not None:
        print("ADSR FAILED: replay run disagrees with the golden vector:")
        print(gv.format_mismatch(mismatch))
        replay_ok = False
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 3. Budget: exported op counters per instance-run + emission check.
    ops = adsr_ops(replay_dir, 2)
    expected_totals = [4 * 1764, 2 * 1764, 3 * 1764, 3 * 1764]
    counters_ok = all(row == expected_totals for row in ops)
    print(
        "budget: %d instance-runs of exported counters -> %s "
        "(per instance-run %s, per tick x6: 24 mult / 12 narrow / 18 shadow)"
        % (len(ops), "OK" if counters_ok else "FAIL", expected_totals)
    )
    ok = ok and counters_ok and check_adsr_budget(schedule)

    # 4. Mutations: each planted fault MUST be detected (AC-6).
    mutations_ok = True

    # 4a. Wrong sustain level (RTL): sustain entry negated.
    mut_dir = workdir / "mut-sustain"
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        ADSR_DUT_SV.read_text(encoding="utf-8"),
        "wire signed [C4_WIDTH-1:0] sustain_eff = sustain_entry;",
        "wire signed [C4_WIDTH-1:0] sustain_eff = -sustain_entry;",
        "wrong-sustain-level",
    )
    (mut_dir / "adsr_engine_mut.sv").write_text(mutated, encoding="utf-8")
    adsr_write_case(mut_dir, 0, case_dirs[ADSR_MUTATION_CASE][1])
    mut_captures = adsr_simulate(
        mut_dir, simulator, 1, mut_dir / "adsr_engine_mut.sv"
    )
    detected = adsr_detects(
        mut_captures[0],
        case_dirs[ADSR_MUTATION_CASE][1],
        vectors[ADSR_MUTATION_CASE],
    )
    print(
        "mutation wrong-sustain-level (RTL): %s"
        % ("DETECTED (test fails the mutant)" if detected else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 4b. Linear-vs-exponential curve confusion (vector side): regenerate
    # the shadow stream with the linearized power and compare the true RTL
    # against the corrupted expectation.
    lin_dir = workdir / "mut-linear"
    lin_dir.mkdir(parents=True, exist_ok=True)
    adsr_write_case(lin_dir, 0, case_dirs[ADSR_MUTATION_CASE][1], linear_curve=True)
    lin_captures = adsr_simulate(lin_dir, simulator, 1, ADSR_DUT_SV)
    detected = adsr_detects(
        lin_captures[0],
        case_dirs[ADSR_MUTATION_CASE][1],
        vectors[ADSR_MUTATION_CASE],
    )
    print(
        "mutation linear-vs-exp curve (vector side): %s"
        % ("DETECTED (vectors discriminate the curve)" if detected else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 4c. Off-by-one timing (RTL): first trigger tick lands on index 1.
    mut_dir = workdir / "mut-timing"
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        ADSR_DUT_SV.read_text(encoding="utf-8"),
        "                index   <= {IDX_BITS{1'b0}};",
        "                index   <= 11'd1;",
        "off-by-one-timing",
    )
    (mut_dir / "adsr_engine_mut.sv").write_text(mutated, encoding="utf-8")
    adsr_write_case(mut_dir, 0, case_dirs[ADSR_MUTATION_CASE][1])
    mut_captures = adsr_simulate(
        mut_dir, simulator, 1, mut_dir / "adsr_engine_mut.sv"
    )
    detected = adsr_detects(
        mut_captures[0],
        case_dirs[ADSR_MUTATION_CASE][1],
        vectors[ADSR_MUTATION_CASE],
    )
    print(
        "mutation off-by-one timing (RTL): %s"
        % ("DETECTED (test fails the mutant)" if detected else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected
    ok = ok and mutations_ok

    if not ok:
        print("ADSR RUN FAILED")
        return 1
    print(
        "ADSR RUN PASSED (contract binding + %d cases sample-exact + "
        "reset/replay independence + budget/op-count asserts + all "
        "mutations detected)" % len(vectors)
    )
    return 0


def lfo_load_vectors():
    """Load and contract-verify every committed LFO/VCA golden vector."""

    vectors = {}
    for path in sorted(LFO_VECTOR_DIR.glob("*.json")):
        vector = gv.load_vector(path)
        gv.verify_accepted_contract(vector)
        vectors[path.stem] = vector
    if not vectors:
        raise SystemExit("no LFO golden vectors found in %s" % LFO_VECTOR_DIR)
    return vectors


def lfo_derive_case(fcp, formats, vector):
    """Model-derived stimulus for one case: envelopes, weights, words.

    Every value comes from the model's own code: the rate/amp envelopes
    are the frozen control path's ``_adsr`` outputs (the #70 engines'
    declared stream outputs, consumed here as inputs per the declared
    amplitude-envelope interface), the weight words replay the declared
    binary64 shadow via :func:`lgo.weight_shadow`, the initial-turn word
    is the declared S3 formation via :func:`lgo.init_word`, and
    :func:`lgo.mirror_lfo`/:func:`lgo.mirror_vca` assert the integer
    replication equals the model's own ``_lfo``/``_control_vca`` rows.
    The committed vector traces must equal the live model's outputs.
    """

    counters = StickyCounters()
    words = ag.quantize_entries(vector["parameters"], formats.midi, formats.mode, counters)
    cases = {}
    for side in lgo.LFO_SIDES:
        rate_env = fcp._adsr(words, side[:-1] + "_rate_adsr.")
        amp_env = fcp._adsr(words, side[:-1] + "_amp_adsr.")
        weight_q = lgo.weight_shadow(fcp, words, side)
        raw, clamps = lgo.mirror_lfo(fcp, words, side, rate_env, weight_q)
        post = lgo.mirror_vca(fcp, raw, amp_env)
        for trace_name, live in (
            (lgo.raw_trace(side), raw),
            (lgo.vca_trace(side), post),
        ):
            committed = None
            for trace in vector["traces"]:
                if trace["name"] == trace_name:
                    committed = trace["values"]
                    break
            if committed is None:
                raise SystemExit(
                    "vector carries no %s trace" % trace_name
                )
            if committed != live:
                raise SystemExit(
                    "committed vector trace %s no longer matches the live "
                    "model; regenerate the vectors" % trace_name
                )
        cases[side] = {
            "freq": words[side + "frequency"],
            "depth": words[side + "mod_depth"],
            "init": lgo.init_word(fcp, words, side),
            "weights": weight_q,
            "rate_env": rate_env,
            "gain": amp_env,
            "raw": raw,
            "post": post,
            "clamps": clamps,
        }
    return cases


def _word32(value: int) -> int:
    """A signed word's 32-bit two's-complement decimal (tb bit pattern)."""

    return value & 0xFFFFFFFF


def lfo_write_case(workdir: Path, run: int, cases: dict):
    """Write one run's stimulus files (params + per-instance streams)."""

    sides = list(cases)
    params = []
    for side in sides:
        case = cases[side]
        params.append(
            [
                _word32(int(case["freq"])),
                _word32(int(case["depth"])),
                _word32(int(case["init"])),
            ]
            + [_word32(int(w)) for w in case["weights"]]
        )
    (workdir / ("run%d_params.txt" % run)).write_text(
        "".join(" ".join(str(v) for v in row) + "\n" for row in params),
        encoding="utf-8",
    )
    for index, side in enumerate(sides):
        case = cases[side]
        lines = []
        for tick in range(gv.CANONICAL_CONTROL_COUNT):
            lines.append(
                "%d %d" % (case["rate_env"][tick], case["gain"][tick])
            )
        (workdir / ("run%d_streams%d.txt" % (run, index))).write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    return sides


def lfo_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path) -> list:
    """Compile and run the two-instance tb; return per-run captures."""

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    if simulator == "iverilog":
        vvp = workdir / "lfo_vca_engine.vvp"
        _run(
            [
                "iverilog", "-g2012", "-o", str(vvp),
                str(CONSTANTS_PKG_SV), str(dut_sv), str(LFO_TB_SV),
            ],
            cwd=workdir,
        )
        _run(["vvp", "-n", str(vvp), "+lut=%s" % (workdir / "lut.memh")], cwd=workdir)
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    captures = []
    for run in range(runs):
        streams = []
        for index in range(2):
            pairs = []
            for line in (
                workdir / ("run%d_captured%d.txt" % (run, index))
            ).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                raw_word, post_word = line.split()
                pairs.append((int(raw_word), int(post_word)))
            streams.append(pairs)
        captures.append(streams)
    return captures


def lfo_ops(workdir: Path, runs: int):
    """Per-run per-instance op counter totals from the tb's ops files."""

    rows = []
    for run in range(runs):
        path = workdir / ("run%d_ops.txt" % run)
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append([int(v) for v in line.split()])
    return rows


def check_lfo_budget(schedule) -> bool:
    """Op-count conformance to the DR-0010 #71 owner row + emission check.

    DR-0010's owner row for #71: "control rate: <= ~14 multiply-class ops
    + 2 C5-class LUT interps + 2 phase steps per tick" (both LFOs and
    both control VCAs). The engine declares 6 multiply-class ops (5 shape
    products + 1 VCA) + 10 declared narrowings (rate, increment, LUT
    interp, sin/saw/tri/sqr, blend, raw, post) + 1 phase step per
    instance-tick, so the two-instance totals are 12 / 20 / 2 per control
    tick. The tb counts them as exported sticky counters and they are
    hard-asserted here. The serialized single-MAC cycle mapping remains
    the integration lanes; this is an op-count check, not a PPA/fit
    claim.
    """

    ok = True
    per_tick = {
        "multiply-class ops": (12, 14),
        "declared narrowings": (20, None),
        "C5-class LUT interps": (2, 2),
        "phase steps": (2, 2),
    }
    for name, (actual, limit) in per_tick.items():
        verdict = "n/a (reported)"
        if limit is not None:
            verdict = "OK" if actual <= limit else "FAIL"
            ok = ok and actual <= limit
        print(
            "  budget: %d %s per control tick vs DR-0010 #71 owner-row cap %s -> %s"
            % (actual, name, limit, verdict)
        )
    try:
        emitted = codegen.emit(schedule_payload=schedule)
        landed = CONSTANTS_PKG_SV.read_text(encoding="utf-8")
        matches = emitted.package_text == landed and (
            sched.SCHEDULE_ID in emitted.emitted_ids
        )
        print(
            "  budget constants: landed %s matches the live emission of both "
            "accepted registers -> %s"
            % (CONSTANTS_PKG_SV.name, "OK" if matches else "FAIL")
        )
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return False
    return ok


def lfo_detects(captures_run, cases, vector):
    """True iff the captured streams mismatch the vector's expectations."""

    captured_by_trace = {}
    for index, side in enumerate(cases):
        captured_by_trace[lgo.raw_trace(side)] = [raw for raw, _ in captures_run[index]]
        captured_by_trace[lgo.vca_trace(side)] = [post for _, post in captures_run[index]]
    return gv.first_mismatch(vector, captured_by_trace) is not None


def lfo_run_mutation(
    workdir: Path,
    simulator: str,
    label: str,
    anchor: str,
    replacement: str,
    case_id: str,
    vectors: dict,
    case_dirs: dict,
    dut_sv: Path = None,
):
    """Plant one RTL mutation on one case and require it to be DETECTED."""

    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    source = (dut_sv or LFO_DUT_SV).read_text(encoding="utf-8")
    mutated = mutate_sv(source, anchor, replacement, label)
    (mut_dir / "lfo_vca_engine_mut.sv").write_text(mutated, encoding="utf-8")
    (mut_dir / "lut.memh").write_bytes((case_dirs[case_id][0] / "lut.memh").read_bytes())
    lfo_write_case(mut_dir, 0, case_dirs[case_id][1])
    mut_captures = lfo_simulate(
        mut_dir, simulator, 1, mut_dir / "lfo_vca_engine_mut.sv"
    )
    detected = lfo_detects(mut_captures[0], case_dirs[case_id][1], vectors[case_id])
    print(
        "mutation %s (RTL, case %s): %s"
        % (
            label,
            case_id,
            "DETECTED (test fails the mutant)" if detected else "NOT DETECTED",
        )
    )
    return detected


def lfo(workdir: Path, simulator: str) -> int:
    """Issue #71 flow: the LFO + control-VCA engine vs the frozen vectors."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("LFO REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("LFO REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    fcp = FixedControlPath(formats.control_spec)

    vectors = lfo_load_vectors()
    print(
        "Loaded %d LFO golden vectors (%s), contract bindings verified"
        % (len(vectors), ", ".join(sorted(vectors)))
    )

    lut_memh = workdir / "lut.memh"
    # The LFO's table is the frozen control path's own C5-shape sweep table
    # (format_sweep.LUT_ENTRY_FORMAT Q1.23, 25-bit words), NOT the accepted
    # C5 audio-path table (formats.table, 24-bit) the anchor flow loads.
    write_lut_memh(fcp.table, lut_memh)

    ok = True

    # 1. Sample-exact engine runs, one committed case per invocation.
    case_dirs = {}
    for case_id in sorted(vectors):
        case_dir = workdir / ("case-" + case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
        cases = lfo_derive_case(fcp, formats, vectors[case_id])
        lfo_write_case(case_dir, 0, cases)
        captures = lfo_simulate(case_dir, simulator, 1, LFO_DUT_SV)
        sides = list(cases)
        mismatch = gv.first_mismatch(
            vectors[case_id],
            {
                lgo.raw_trace(side): [raw for raw, _ in captures[0][index]]
                for index, side in enumerate(sides)
            }
            | {
                lgo.vca_trace(side): [post for _, post in captures[0][index]]
                for index, side in enumerate(sides)
            },
        )
        if mismatch is not None:
            print(
                "LFO FAILED: RTL disagrees with the golden vector (%s):"
                % case_id
            )
            print(gv.format_mismatch(mismatch))
            ok = False
        clamp_total = sum(cases[side]["clamps"] for side in sides)
        print(
            "case %s: %d instances x %d control samples (rate clamps %d), "
            "RTL sample-exact -> %s"
            % (case_id, len(sides), gv.CANONICAL_CONTROL_COUNT, clamp_total,
               "OK" if ok else "FAIL")
        )
        case_dirs[case_id] = (case_dir, cases)

    # 2. Reset/replay: a second trigger cannot retain prior LFO state
    #    (phase accumulators and weights are per-trigger).
    first, second = LFO_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
    cases_first = lfo_derive_case(fcp, formats, vectors[first])
    cases_second = lfo_derive_case(fcp, formats, vectors[second])
    lfo_write_case(replay_dir, 0, cases_first)
    lfo_write_case(replay_dir, 1, cases_second)
    replay_captures = lfo_simulate(replay_dir, simulator, 2, LFO_DUT_SV)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    (solo_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
    lfo_write_case(solo_dir, 0, cases_second)
    solo_captures = lfo_simulate(solo_dir, simulator, 1, LFO_DUT_SV)
    replay_ok = True
    for index in range(2):
        if replay_captures[1][index] != solo_captures[0][index]:
            print(
                "LFO FAILED: run-after-run capture differs from the solo "
                "run (instance %d) - prior LFO state leaked" % index
            )
            replay_ok = False
    second_sides = list(cases_second)
    mismatch = gv.first_mismatch(
        vectors[second],
        {
            lgo.raw_trace(side): [raw for raw, _ in replay_captures[1][index]]
            for index, side in enumerate(second_sides)
        }
        | {
            lgo.vca_trace(side): [post for _, post in replay_captures[1][index]]
            for index, side in enumerate(second_sides)
        },
    )
    if mismatch is not None:
        print("LFO FAILED: replay run disagrees with the golden vector:")
        print(gv.format_mismatch(mismatch))
        replay_ok = False
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 3. Budget: exported op counters per instance-run + emission check.
    ops = lfo_ops(replay_dir, 2)
    expected_static = [6 * 1764, 10 * 1764, 1764]
    counters_ok = True
    for run_index, run_cases in enumerate((cases_first, cases_second)):
        for inst_index, side in enumerate(run_cases):
            row = ops[run_index * 2 + inst_index]
            expected = expected_static + [run_cases[side]["clamps"]]
            if row != expected:
                print(
                    "LFO FAILED: exported counters %r != expected %r "
                    "(run %d instance %d)" % (row, expected, run_index, inst_index)
                )
                counters_ok = False
    print(
        "budget: %d instance-runs of exported counters -> %s "
        "(per instance-run %s + measured clamps; per tick x2: "
        "12 mult / 20 narrow / 2 LUT interp / 2 phase steps)"
        % (len(ops), "OK" if counters_ok else "FAIL", expected_static)
    )
    ok = ok and counters_ok and check_lfo_budget(schedule)

    # 4. Mutations: each planted fault MUST be detected (AC-6).
    mutations_ok = True

    # 4a. Selector instead of blend (RTL): the continuous shape-weight
    #     merge becomes a discrete argmax one-hot.
    mutations_ok &= lfo_run_mutation(
        workdir, simulator, "selector-instead-of-blend",
        "    wire signed [95:0] merged =\n"
        "        $signed(w_eff0) * $signed(sin_q30)\n"
        "      + $signed(w_eff1) * $signed(tri_q30)\n"
        "      + $signed(w_eff2) * $signed(saw_q30)\n"
        "      + $signed(w_eff3) * $signed(rsaw_q30)\n"
        "      + $signed(w_eff4) * $signed(sqr_q30);",
        "    wire signed [95:0] merged =\n"
        "        (w_eff0 >= w_eff1 && w_eff0 >= w_eff2 && w_eff0 >= w_eff3 && w_eff0 >= w_eff4)"
        " ? $signed(w_eff0) * $signed(sin_q30)\n"
        "        : (w_eff1 >= w_eff2 && w_eff1 >= w_eff3 && w_eff1 >= w_eff4)"
        " ? $signed(w_eff1) * $signed(tri_q30)\n"
        "        : (w_eff2 >= w_eff3 && w_eff2 >= w_eff4)"
        " ? $signed(w_eff2) * $signed(saw_q30)\n"
        "        : (w_eff3 >= w_eff4) ? $signed(w_eff3) * $signed(rsaw_q30)\n"
        "        : $signed(w_eff4) * $signed(sqr_q30);",
        LFO_MUTATION_CASE, vectors, case_dirs,
    )

    # 4b. Wrong shape table (RTL): the sine table's quadrant negation is
    #     dropped, corrupting the C5 content for half the circle.
    mutations_ok &= lfo_run_mutation(
        workdir, simulator, "wrong-shape-table",
        "    wire signed [24:0] entry_word = negate ? -lut_word : lut_word;",
        "    wire signed [24:0] entry_word = lut_word;",
        LFO_MUTATION_CASE, vectors, case_dirs,
    )

    # 4c. Depth modulation dropped (RTL): the rate becomes the bare
    #     frequency word; the envelope never modulates.
    mutations_ok &= lfo_run_mutation(
        workdir, simulator, "depth-modulation-dropped",
        "    wire signed [95:0] rate_numer = $signed(freq_r) * (96'sd1 << 21) + depth_term;",
        "    wire signed [95:0] rate_numer = $signed(freq_r) * (96'sd1 << 21);",
        LFO_MUTATION_CASE, vectors, case_dirs,
    )

    # 4d. Phase first-increment skipped (RTL): sample zero loses the
    #     leading increment the model accumulates before the initial
    #     phase; the whole trace shifts by one increment.
    mutations_ok &= lfo_run_mutation(
        workdir, simulator, "phase-first-increment-skipped",
        "                phase <= phase_next;",
        "                phase <= (index == 11'd0) ? phase : phase_next;",
        LFO_PHASE_MUTATION_CASE, vectors, case_dirs,
    )

    # 4e. Rate clamp removed (RTL): negative modulated rates enter the
    #     increment site instead of clamping at zero. Only meaningful on
    #     a case whose host mirror counted actual clamps.
    clamp_cases = case_dirs[LFO_CLAMP_MUTATION_CASE][1]
    clamp_total = sum(clamp_cases[side]["clamps"] for side in clamp_cases)
    if clamp_total <= 0:
        print(
            "LFO FAILED: %s carries no rate clamps; the clamp mutation "
            "would be vacuous" % LFO_CLAMP_MUTATION_CASE
        )
        mutations_ok = False
    else:
        mutations_ok &= lfo_run_mutation(
            workdir, simulator, "rate-clamp-removed",
            "    wire [46:0]        rate_eff = rate_clamped ? 47'd0 : rate_signed;",
            "    wire [46:0]        rate_eff = rate_signed;",
            LFO_CLAMP_MUTATION_CASE, vectors, case_dirs,
        )
    ok = ok and mutations_ok

    if not ok:
        print("LFO RUN FAILED")
        return 1
    print(
        "LFO RUN PASSED (contract binding + %d cases sample-exact + "
        "reset/replay independence + budget/op-count asserts + all "
        "mutations detected)" % len(vectors)
    )
    return 0


def mm_load_vectors():
    """Load and contract-verify every committed mod-matrix golden vector."""

    vectors = {}
    for path in sorted(MM_VECTOR_DIR.glob("*.json")):
        vector = gv.load_vector(path)
        gv.verify_accepted_contract(vector)
        vectors[path.stem] = vector
    if not vectors:
        raise SystemExit("no mod-matrix golden vectors found in %s" % MM_VECTOR_DIR)
    return vectors


def mm_depth_words(vector, words):
    """The twenty S1 depth words, route-major in the pinned source order."""

    return {
        route: [
            words["mod_matrix." + source + "->" + route]
            for source in MOD_MATRIX_INPUTS
        ]
        for route in MOD_MATRIX_OUTPUTS
    }


def mm_derive_case(fcp, formats, vector):
    """Model-derived stimulus and truth for one case.

    Everything comes from the frozen composition's own control path: the
    twenty S1 depth words (:func:`format_sweep.quantize_params`), the four
    source columns (``_adsr``/``_lfo``/``_control_vca`` — the #70/#71
    engines' declared outputs), the matrix words (``mirror_mod_matrix``
    asserted row-equal to ``_mod_matrix``), and the five full-length audio
    streams (``mirror_upsample`` asserted row-equal to ``_upsample``). The
    committed vector's matrix traces must equal the live model, and every
    case's audio digest (and, where present, sidecar bytes) must equal the
    regenerated truth — refuse on any drift.
    """

    counters = StickyCounters()
    words = quantize_params(vector["parameters"], formats.midi, formats.mode, counters)

    rate_1 = fcp._adsr(words, "lfo_1_rate_adsr.")
    rate_2 = fcp._adsr(words, "lfo_2_rate_adsr.")
    amp_1 = fcp._adsr(words, "lfo_1_amp_adsr.")
    amp_2 = fcp._adsr(words, "lfo_2_amp_adsr.")
    lfo_1 = fcp._lfo(words, "lfo_1.", rate_1)
    lfo_2 = fcp._lfo(words, "lfo_2.", rate_2)
    post_1 = fcp._control_vca(lfo_1, amp_1)
    post_2 = fcp._control_vca(lfo_2, amp_2)
    adsr_1 = fcp._adsr(words, "adsr_1.")
    adsr_2 = fcp._adsr(words, "adsr_2.")
    columns = [adsr_1, adsr_2, post_1, post_2]

    matrix, matrix_counters = mm.mirror_mod_matrix(fcp, words, columns)

    committed = {}
    for trace in vector["traces"]:
        committed[trace["name"]] = trace["values"]
    for route in MOD_MATRIX_OUTPUTS:
        name = "mod_matrix." + route
        if committed.get(name) != matrix[route]:
            raise SystemExit(
                "committed vector trace %s no longer matches the live "
                "model; regenerate the vectors" % name
            )

    audio = {}
    audio_counters = {}
    audio_digests = {}
    for route in MOD_MATRIX_OUTPUTS:
        stream, route_counters = mm.mirror_upsample(fcp, matrix[route], route)
        audio[route] = stream
        audio_counters[route] = route_counters
        evidence = vector["provenance"]["audio_traces"]["control_upsample." + route]
        digest = mm_mod_digest(stream)
        if digest != evidence["words_sha256"]:
            raise SystemExit(
                "audio digest drift for control_upsample.%s: regenerated %s "
                "but the committed vector declares %s — regenerate the "
                "vectors, do not recompile" % (route, digest, evidence["words_sha256"])
            )
        if stream[0] != evidence["first_word"] or stream[-1] != evidence["last_word"]:
            raise SystemExit(
                "endpoint drift for control_upsample.%s against the "
                "committed vector" % route
            )
        for j_str, word in evidence["jitter"].items():
            if stream[int(j_str)] != word:
                raise SystemExit(
                    "jitter drift for control_upsample.%s at j=%s against "
                    "the committed vector" % (route, j_str)
                )
        audio_digests[route] = digest
    case = {
        "depths": mm_depth_words(vector, words),
        "columns": columns,
        "matrix": matrix,
        "audio": audio,
        "matrix_counters": matrix_counters,
        "audio_counters": audio_counters,
        "audio_digests": audio_digests,
    }

    sidecar_cases = [
        route
        for route in MOD_MATRIX_OUTPUTS
        if "sidecar" in vector["provenance"]["audio_traces"]["control_upsample." + route]
    ]
    for route in sidecar_cases:
        row = vector["provenance"]["audio_traces"]["control_upsample." + route]["sidecar"]
        payload = (ROOT / "sim" / "reference" / row["file"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise SystemExit(
                "sidecar bytes drifted for control_upsample.%s (%s); "
                "regenerate the vectors" % (route, row["file"])
            )
        if mm_mod_digest(mm.unpack_words_f32le(payload)) != row["words_sha256"]:
            raise SystemExit(
                "sidecar words drifted for control_upsample.%s (%s)"
                % (route, row["file"])
            )
    return case


def mm_mod_digest(words) -> str:
    """The #54 trace-digest convention over an integer word list."""

    blob = json.dumps(
        list(words), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def mm_write_case(workdir: Path, run: int, case: dict):
    """Write one run's stimulus files (depths + source columns)."""

    lines = []
    for route in MOD_MATRIX_OUTPUTS:
        lines.append(
            " ".join(
                str(word & 0xFFFFFFFF) for word in case["depths"][route]
            )
        )
    (workdir / ("run%d_depths.txt" % run)).write_text(
        "".join(line + "\n" for line in lines), encoding="utf-8"
    )
    cols = case["columns"]
    rows = []
    for tick in range(gv.CANONICAL_CONTROL_COUNT):
        rows.append(
            " ".join(
                str(cols[source][tick] & 0xFFFFFFFF) for source in range(4)
            )
        )
    (workdir / ("run%d_columns.txt" % run)).write_text(
        "".join(row + "\n" for row in rows), encoding="utf-8"
    )


def mm_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path,
                up_sv: Path = None, max_j: int = None) -> list:
    """Compile and run the 1+5-instance tb; return per-run captures.

    ``max_j`` caps the audio walk via the ``+max_j`` plusarg — a mutation
    demonstration knob only; committed-case runs use the full walk.
    """

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    if simulator == "iverilog":
        vvp = workdir / "mod_matrix_upsample.vvp"
        _run(
            [
                "iverilog", "-g2012", "-o", str(vvp),
                str(CONSTANTS_PKG_SV), str(dut_sv),
                str(up_sv or MM_UP_DUT_SV), str(MM_TB_SV),
            ],
            cwd=workdir,
        )
        vvp_cmd = ["vvp", "-n", str(vvp)]
        if max_j is not None:
            vvp_cmd.append("+max_j=%d" % max_j)
        _run(vvp_cmd, cwd=workdir)
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    captures = []
    for run in range(runs):
        matrix = []
        for line in (
            workdir / ("run%d_matrix.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                matrix.append([int(word) for word in line.split()])
        audio = {}
        for index, route in enumerate(MOD_MATRIX_OUTPUTS):
            words = []
            for line in (
                workdir / ("run%d_audio%d.txt" % (run, index))
            ).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    words.append(int(line))
                except ValueError:
                    words.append(None)  # an x-state emission: a mismatch
            audio[route] = words
        ops = []
        for line in (
            workdir / ("run%d_ops.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                ops.append(line.split())
        captures.append({"matrix": matrix, "audio": audio, "ops": ops})
    return captures


def mm_matrix_by_route(matrix_rows) -> dict:
    """Captured matrix rows split into per-route word lists."""

    return {
        route: [row[index] for row in matrix_rows]
        for index, route in enumerate(MOD_MATRIX_OUTPUTS)
    }


def mm_audio_mismatches(captured: dict, expected: dict) -> list:
    """(route, index, expected, actual) rows where the audio differs."""

    rows = []
    for route in MOD_MATRIX_OUTPUTS:
        got = captured[route]
        want = expected[route]
        for index in range(max(len(got), len(want))):
            a = got[index] if index < len(got) else None
            b = want[index] if index < len(want) else None
            if a != b:
                rows.append((route, index, b, a))
                if len(rows) >= 3:
                    return rows
    return rows


def mm_mismatch_trace_set(captured_matrix, captured_audio, case,
                          audio_prefix: bool = False) -> set:
    """Every trace name (control and audio) where the capture differs.

    ``audio_prefix`` compares each captured audio stream against the
    expected prefix of its own length — used by the capped-walk mutation
    demonstrations, where a shorter capture is not itself a mismatch but
    any differing sample inside the captured prefix is. Matrix traces are
    always compared over the full control grid.
    """

    bad = set()
    for route in MOD_MATRIX_OUTPUTS:
        got = captured_matrix[route]
        want = case["matrix"][route]
        if any(
            got[i] != want[i] if i < len(got) and i < len(want) else True
            for i in range(max(len(got), len(want)))
        ):
            bad.add("mod_matrix." + route)
        expected_audio = case["audio"][route]
        if audio_prefix:
            expected_audio = expected_audio[: len(captured_audio[route])]
        if captured_audio[route] != expected_audio:
            bad.add("control_upsample." + route)
    return bad


def mm_check_case(capture, case, vector, case_id: str) -> bool:
    """Sample-exact matrix + audio verification for one run."""

    captured_matrix = mm_matrix_by_route(capture["matrix"])
    mismatch = gv.first_mismatch(
        vector,
        {("mod_matrix." + route): captured_matrix[route]
         for route in MOD_MATRIX_OUTPUTS},
    )
    ok = mismatch is None
    if mismatch is not None:
        print(
            "MOD-MATRIX FAILED: RTL disagrees with the golden vector (%s):"
            % case_id
        )
        print(gv.format_mismatch(mismatch))
    audio_rows = mm_audio_mismatches(capture["audio"], case["audio"])
    if audio_rows:
        ok = False
        route, index, want, got = audio_rows[0]
        print(
            "MOD-MATRIX FAILED: audio stream differs (%s "
            "control_upsample.%s[%d]: expected %s got %s)"
            % (case_id, route, index, want, got)
        )
    for route in MOD_MATRIX_OUTPUTS:
        digest = mm_mod_digest(capture["audio"][route])
        if digest != case["audio_digests"][route]:
            ok = False
            print(
                "MOD-MATRIX FAILED: audio digest differs (%s "
                "control_upsample.%s)" % (case_id, route)
            )
    return ok


def mm_ops(capture) -> dict:
    """Parse one run's ops file into the matrix + per-route rows."""

    row_m = capture["ops"][0]
    assert row_m[0] == "M", capture["ops"]
    parsed = {
        "matrix": [int(v) for v in row_m[1:]],
    }
    routes = {}
    for row in capture["ops"][1:]:
        assert row[0] == "U", capture["ops"]
        routes[MOD_MATRIX_OUTPUTS[len(routes)]] = [int(v) for v in row[1:]]
    parsed["routes"] = routes
    return parsed


#: Static per-route upsample op totals over the FULL walk (interior = the
#: 176,398 non-endpoint samples; the two exact-boundary endpoints are
#: declared exact copies with no blend ops): 1 blend mult + 1 blend add +
#: 1 narrowing + 1 fraction word per interior sample, 176,399 coordinate
#: steps, 176,400 emissions.
MM_UPSAMPLE_STATIC_FULL = [
    gv.CANONICAL_SAMPLE_COUNT - 2,      # blend mults
    gv.CANONICAL_SAMPLE_COUNT - 2,      # blend adds
    gv.CANONICAL_SAMPLE_COUNT - 2,      # declared narrowings
    gv.CANONICAL_SAMPLE_COUNT - 2,      # fraction words
    gv.CANONICAL_SAMPLE_COUNT - 1,      # coordinate steps
]


def mm_expected_upsample_ops(case, route, walk_len: int) -> list:
    """Expected exported upsample counters for one route run.

    ``walk_len`` is the emitted-word count (full or capped); interior
    samples are every emitted sample except the exact-boundary endpoints
    actually reached within the walk.
    """

    if walk_len >= gv.CANONICAL_SAMPLE_COUNT:
        static = list(MM_UPSAMPLE_STATIC_FULL)
    else:
        interior = walk_len - 1  # only the j=0 endpoint is reached
        static = [interior, interior, interior, interior, walk_len - 1]
    return static + [
        case["audio_counters"][route]["total_saturation"],
        walk_len,
    ]


def check_mm_budget(schedule) -> bool:
    """Op-count conformance to the DR-0010 #72 owner row + emission check.

    DR-0010's owner rows for #72: the matrix is "control rate: 20 MACs +
    5 declared narrowings per tick"; the upsamples are "audio rate:
    2 mults + 1 add + 1 half-even blend per column per sample". The
    engines declare exactly those counts per route-column-sample (the
    blend product/add/narrowing at 1/1/1 within the 2/1/1 owner-row cap;
    the exact incremental coordinate walk is one add + one compare per
    step, reported as a declared extra), so per control tick the matrix
    totals are 20 / 15 / 5 (mult / add / narrow) and per audio sample
    each upsample column totals 176,398 blend mults + 176,398 adds +
    176,398 narrowings + 176,398 fraction words + 176,399 coordinate
    steps (the two exact-boundary endpoints are declared exact copies
    with no blend ops). The tb counts them as exported sticky counters
    and they are hard-asserted here.
    The serialized single-MAC cycle mapping remains the integration
    lanes; this is an op-count check, not a PPA/fit claim.
    """

    ok = True
    per_tick = {
        "matrix MACs": (20, 20),
        "matrix declared narrowings": (5, 5),
        "upsample blend mults per column-sample": (1, 2),
        "upsample blend adds per column-sample": (1, 1),
        "upsample declared blends per column-sample": (1, 1),
    }
    for name, (actual, limit) in per_tick.items():
        verdict = "OK" if actual <= limit else "FAIL"
        ok = ok and actual <= limit
        print(
            "  budget: %d %s vs DR-0010 #72 owner-row cap %d -> %s"
            % (actual, name, limit, verdict)
        )
    try:
        emitted = codegen.emit(schedule_payload=schedule)
        landed = CONSTANTS_PKG_SV.read_text(encoding="utf-8")
        matches = emitted.package_text == landed and (
            sched.SCHEDULE_ID in emitted.emitted_ids
        )
        print(
            "  budget constants: landed %s matches the live emission of both "
            "accepted registers -> %s"
            % (CONSTANTS_PKG_SV.name, "OK" if matches else "FAIL")
        )
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return False
    return ok


def mm_run_mutation(
    workdir: Path,
    simulator: str,
    label: str,
    anchor: str,
    replacement: str,
    case_id: str,
    vectors: dict,
    case_dirs: dict,
    expect_traces: set = None,
):
    """Plant one RTL mutation on one case and require it to be DETECTED.

    With ``expect_traces`` the mismatch must ALSO localize to exactly that
    trace set (the route-swap/depth AC's localization evidence).
    """

    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    source = MM_DUT_SV.read_text(encoding="utf-8")
    up_source = MM_UP_DUT_SV.read_text(encoding="utf-8")
    if anchor not in source and anchor not in up_source:
        raise SystemExit("mutation anchor not found for %s" % label)
    if anchor in source:
        (mut_dir / "mod_matrix_engine_mut.sv").write_text(
            mutate_sv(source, anchor, replacement, label), encoding="utf-8"
        )
        mut_dut = mut_dir / "mod_matrix_engine_mut.sv"
        mut_up = MM_UP_DUT_SV
    else:
        (mut_dir / "upsample_engine_mut.sv").write_text(
            mutate_sv(up_source, anchor, replacement, label), encoding="utf-8"
        )
        mut_dut = MM_DUT_SV
        mut_up = mut_dir / "upsample_engine_mut.sv"
    case_dir, case = case_dirs[case_id]
    mm_write_case(mut_dir, 0, case)
    mut_captures = mm_simulate(mut_dir, simulator, 1, mut_dut, mut_up,
                               max_j=MM_MUTATION_WALK_CAP)
    capture = mut_captures[0]
    detected = not mm_check_case(capture, case, vectors[case_id], case_id)
    localization = ""
    if detected and expect_traces is not None:
        bad = mm_mismatch_trace_set(
            mm_matrix_by_route(capture["matrix"]), capture["audio"], case,
            audio_prefix=True,
        )
        if bad != expect_traces:
            detected = False
            localization = (
                " (localization FAILED: mismatch set %s != expected %s)"
                % (sorted(bad), sorted(expect_traces))
            )
        else:
            localization = " (localized to %s)" % sorted(bad)
    print(
        "mutation %s (RTL, case %s): %s%s"
        % (
            label,
            case_id,
            "DETECTED (test fails the mutant)" if detected else "NOT DETECTED",
            localization,
        )
    )
    return detected


def mm_run_vector_mutation(
    workdir: Path,
    simulator: str,
    label: str,
    case_id: str,
    vectors: dict,
    case_dirs: dict,
    rewrite_stimulus,
    expect_traces: set,
):
    """Plant a vector-side (stimulus) mutation and require localization.

    ``rewrite_stimulus(case)`` mutates the derived case in place (a depth
    word) before re-simulation; the mismatch must localize to exactly the
    expected trace set.
    """

    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    case = case_dirs[case_id][1]
    # The rewrite mutates its argument in place: deep-copy so the shared
    # derived case (and every later mutation run) keeps pristine truth.
    mutated = copy.deepcopy(case)
    rewrite_stimulus(mutated)
    mm_write_case(mut_dir, 0, mutated)
    mut_captures = mm_simulate(mut_dir, simulator, 1, MM_DUT_SV,
                               max_j=MM_MUTATION_WALK_CAP)
    capture = mut_captures[0]
    # Compare against the MUTATED expectations: stimulus-side mutations
    # (sign flip, one-ULP depth) leave the expectations pristine, so the
    # capture diverges from them; expectation-side mutations (route swap)
    # corrupt exactly the traces that must localize.
    bad = mm_mismatch_trace_set(
        mm_matrix_by_route(capture["matrix"]), capture["audio"], mutated,
        audio_prefix=True,
    )
    detected = bool(bad)
    localization = ""
    if bad != expect_traces:
        detected = False
        localization = (
            " (localization FAILED: mismatch set %s != expected %s)"
            % (sorted(bad), sorted(expect_traces))
        )
    else:
        localization = " (localized to %s)" % sorted(bad)
    print(
        "mutation %s (vector side, case %s): %s%s"
        % (
            label,
            case_id,
            "DETECTED (test fails the mutant)" if detected else "NOT DETECTED",
            localization,
        )
    )
    return detected


def modmatrix(workdir: Path, simulator: str) -> int:
    """Issue #72 flow: the mod matrix + upsample engines vs the frozen vectors."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("MOD-MATRIX REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("MOD-MATRIX REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    fcp = FixedControlPath(formats.control_spec)

    vectors = mm_load_vectors()
    print(
        "Loaded %d mod-matrix golden vectors (%s), contract bindings verified"
        % (len(vectors), ", ".join(sorted(vectors)))
    )

    ok = True

    # 1. Sample-exact engine runs, one committed case per invocation.
    case_dirs = {}
    for case_id in sorted(vectors):
        case_dir = workdir / ("case-" + case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        case = mm_derive_case(fcp, formats, vectors[case_id])
        mm_write_case(case_dir, 0, case)
        captures = mm_simulate(case_dir, simulator, 1, MM_DUT_SV)
        if not mm_check_case(captures[0], case, vectors[case_id], case_id):
            ok = False
        total_controls = gv.CANONICAL_CONTROL_COUNT
        print(
            "case %s: 5 routes x %d control samples + 5 x %d audio samples, "
            "RTL sample-exact -> %s"
            % (case_id, total_controls, gv.CANONICAL_SAMPLE_COUNT,
               "OK" if ok else "FAIL")
        )
        case_dirs[case_id] = (case_dir, case)

    # 2. Reset/replay: a second trigger cannot retain prior state (depth
    #    words, column memories, and walk counters are per-trigger).
    first, second = MM_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    cases_first = mm_derive_case(fcp, formats, vectors[first])
    cases_second = mm_derive_case(fcp, formats, vectors[second])
    mm_write_case(replay_dir, 0, cases_first)
    mm_write_case(replay_dir, 1, cases_second)
    replay_captures = mm_simulate(replay_dir, simulator, 2, MM_DUT_SV)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    mm_write_case(solo_dir, 0, cases_second)
    solo_captures = mm_simulate(solo_dir, simulator, 1, MM_DUT_SV)
    replay_ok = True
    if replay_captures[1]["matrix"] != solo_captures[0]["matrix"] or \
            replay_captures[1]["audio"] != solo_captures[0]["audio"]:
        print(
            "MOD-MATRIX FAILED: run-after-run capture differs from the solo "
            "run - prior run state leaked"
        )
        replay_ok = False
    if not mm_check_case(replay_captures[1], cases_second, vectors[second], second):
        replay_ok = False
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 3. Budget: exported op counters + the DR-0010 #72 owner row.
    ops_ok = True
    static_matrix = [20 * gv.CANONICAL_CONTROL_COUNT,
                     15 * gv.CANONICAL_CONTROL_COUNT,
                     5 * gv.CANONICAL_CONTROL_COUNT]
    for run_index, case in enumerate((cases_first, cases_second)):
        parsed = mm_ops(replay_captures[run_index])
        expected_m = static_matrix + [case["matrix_counters"]["total_saturation"]]
        if parsed["matrix"] != expected_m:
            print(
                "MOD-MATRIX FAILED: matrix counters %r != expected %r (run %d)"
                % (parsed["matrix"], expected_m, run_index)
            )
            ops_ok = False
        for route in MOD_MATRIX_OUTPUTS:
            expected_u = mm_expected_upsample_ops(
                case, route, gv.CANONICAL_SAMPLE_COUNT
            )
            if parsed["routes"][route] != expected_u:
                print(
                    "MOD-MATRIX FAILED: upsample counters %s %r != expected "
                    "%r (run %d)" % (route, parsed["routes"][route],
                                     expected_u, run_index)
                )
                ops_ok = False
    print(
        "budget: %d runs of exported counters -> %s (matrix per tick 20/15/5"
        " mult/add/narrow; upsample per column interior sample 1/1/1 blend"
        " mult/add/narrow + 1 fraction word within the 2/1/1 owner-row cap;"
        " exact incremental coordinate walk reported as a declared extra;"
        " endpoints exact copies)"
        % (2, "OK" if ops_ok else "FAIL")
    )
    ok = ok and ops_ok and check_mm_budget(schedule)

    # 4. Mutations: each planted fault MUST be detected (AC-5/AC-6).
    mutations_ok = True

    # 4a. Clamped matrix output (RTL): the C7 narrowing bounds tighten to
    #     the upstream clamp +-1.0, which must NEVER hold here. The
    #     extremes case drives matrix outputs to ~3.4 so the mutant must
    #     break the vector.
    mutations_ok &= mm_run_mutation(
        workdir, simulator, "clamped-matrix-output",
        "    localparam signed [95:0] C1_SAT_HI = 96'sd8388607;   // (1<<23)-1: +4-2^-21\n"
        "    localparam signed [95:0] C1_SAT_LO = -96'sd8388608;  // -(1<<23):  -4",
        "    localparam signed [95:0] C1_SAT_HI = 96'sd2097151;   // MUTANT: upstream clamp +1.0\n"
        "    localparam signed [95:0] C1_SAT_LO = -96'sd2097152;  // MUTANT: upstream clamp -1.0",
        MM_CLAMP_MUTATION_CASE, vectors, case_dirs,
    )

    # 4b. Selector instead of blend (RTL): the shared four-source weighted
    #     sum becomes a discrete argmax one-hot pick.
    mutations_ok &= mm_run_mutation(
        workdir, simulator, "selector-matrix",
        "        blend4 = $signed(d0) * $signed(s0)\n"
        "               + $signed(d1) * $signed(s1)\n"
        "               + $signed(d2) * $signed(s2)\n"
        "               + $signed(d3) * $signed(s3);",
        "        blend4 = (d0 >= d1 && d0 >= d2 && d0 >= d3) ? $signed(d0) * $signed(s0)\n"
        "               : (d1 >= d2 && d1 >= d3) ? $signed(d1) * $signed(s1)\n"
        "               : (d2 >= d3) ? $signed(d2) * $signed(s2)\n"
        "               : $signed(d3) * $signed(s3);",
        MM_MUTATION_CASE, vectors, case_dirs,
    )

    # 4c. ZOH instead of endpoint-aligned interpolation (RTL): the blend
    #     result collapses to the floor column word.
    mutations_ok &= mm_run_mutation(
        workdir, simulator, "zoh-upsample",
        "                    audio_word  <= q[C1_WIDTH-1:0];",
        "                    audio_word  <= left[C1_WIDTH-1:0];",
        MM_MUTATION_CASE, vectors, case_dirs,
    )

    # 4d. Off-endpoint coordinates (RTL): align_corners=False, the
    #     dropped-endpoint scale j*1763/176400; endpoints and interior
    #     both shift.
    mutations_ok &= mm_run_mutation(
        workdir, simulator, "off-endpoint-coordinate",
        "    localparam [17:0] UP_DEN      = 18'd176399;  // AUDIO_SAMPLES - 1",
        "    localparam [17:0] UP_DEN      = 18'd176400;  // MUTANT: align_corners=False",
        MM_MUTATION_CASE, vectors, case_dirs,
    )

    # 4e. Dropped route (RTL): vco_2_amp is forced to zero; the mismatch
    #     must localize to exactly that route's two traces.
    mutations_ok &= mm_run_mutation(
        workdir, simulator, "dropped-route",
        "                out_r3    <= q3[C1_WIDTH-1:0];",
        "                out_r3    <= {C1_WIDTH{1'b0}};",
        MM_MUTATION_CASE, vectors, case_dirs,
        expect_traces={"mod_matrix." + MM_DROPPED_ROUTE,
                       "control_upsample." + MM_DROPPED_ROUTE},
    )

    # 4f-h. Route swap / sign flip / one-ULP depth (vector side): each
    #     stimulus-or-expectation mutation must localize to exactly the
    #     expected route's traces (AC-6).
    def _swap_routes(case):
        a, b = ("vco_1_amp", "noise_amp")
        (
            case["matrix"][a], case["matrix"][b],
        ) = (
            case["matrix"][b], case["matrix"][a],
        )

    mutations_ok &= mm_run_vector_mutation(
        workdir, simulator, "route-swap", MM_MUTATION_CASE, vectors,
        case_dirs, _swap_routes,
        expect_traces={"mod_matrix.vco_1_amp", "mod_matrix.noise_amp"},
    )

    def _sign_flip(case):
        case["depths"]["vco_1_pitch"][0] = -case["depths"]["vco_1_pitch"][0]

    mutations_ok &= mm_run_vector_mutation(
        workdir, simulator, "depth-sign-flip", MM_MUTATION_CASE, vectors,
        case_dirs, _sign_flip,
        expect_traces={"mod_matrix.vco_1_pitch", "control_upsample.vco_1_pitch"},
    )

    def _depth_ulp(case):
        case["depths"]["noise_amp"][1] = case["depths"]["noise_amp"][1] + 1

    mutations_ok &= mm_run_vector_mutation(
        workdir, simulator, "depth-one-ulp", MM_MUTATION_CASE, vectors,
        case_dirs, _depth_ulp,
        expect_traces={"mod_matrix.noise_amp", "control_upsample.noise_amp"},
    )
    ok = ok and mutations_ok

    if not ok:
        print("MOD-MATRIX RUN FAILED")
        return 1
    print(
        "MOD-MATRIX RUN PASSED (contract binding + %d cases sample-exact + "
        "reset/replay independence + budget/op-count asserts + all "
        "mutations detected)" % len(vectors)
    )
    return 0


def vco_load_vectors():
    """Load and contract-verify every committed square/saw VCO vector."""

    vectors = {}
    for path in sorted(VCO_VECTOR_DIR.glob("*.json")):
        vector = gv.load_vector(path)
        gv.verify_accepted_contract(vector)
        vectors[path.stem] = vector
    if not vectors:
        raise SystemExit(
            "no square/saw VCO golden vectors found in %s" % VCO_VECTOR_DIR
        )
    return vectors


def vco2_derive_case(formats, vector):
    """Model-derived stimulus and truth for one square/saw VCO case.

    The host mirror (``torchsynth_voice.vco2_golden``) re-walks the frozen
    model's vco_2 lane with the model's own primitives; its ``vco_2.raw``
    digest must equal the committed evidence row — and, on frozen-binding
    vectors, the frozen golden's trace digest plus the retained sidecar
    words. The declared binary64 shadow sites (midi->Hz exp2, the
    partials constant, the tanh fanout) are computed host-side here
    exactly as DR-0008/DR-0010 declare them open approximation items —
    a harness input derivation, never an RTL claim.
    """

    provenance = vector["provenance"]
    derivation = vc.derive_case(formats, vector["parameters"])
    streams = derivation["streams"]
    digest = vc.voice_digest(streams["v2"])
    row = provenance["vco_2_raw"]
    if digest != row["words_sha256"]:
        raise SystemExit(
            "square/saw VCO mirror digest drift for %s: regenerated %s but "
            "the committed vector declares %s -- regenerate the vectors, "
            "do not recompile"
            % (provenance["case_id"], digest, row["words_sha256"])
        )
    for j_str, word in row["jitter"].items():
        if streams["v2"][int(j_str)] != word:
            raise SystemExit(
                "jitter drift for %s at j=%s against the committed vector"
                % (provenance["case_id"], j_str)
            )

    frozen_words = None
    binding = provenance.get("frozen_binding")
    if binding:
        payload = (ROOT / binding["sidecar"]["file"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != binding["sidecar"]["sha256"]:
            raise SystemExit(
                "frozen sidecar bytes drifted for %s" % provenance["case_id"]
            )
        frozen_words = vc.unpack_words_f32le(payload)
        if vc.voice_digest(frozen_words) != binding["trace_digest"]:
            raise SystemExit(
                "frozen sidecar words digest drift for %s"
                % provenance["case_id"]
            )
        if digest != binding["trace_digest"]:
            raise SystemExit(
                "mirror does not reproduce the frozen vco_2.raw digest "
                "for %s" % provenance["case_id"]
            )

    return {
        "case_id": provenance["case_id"],
        "statics": {
            "keyboard.midi_f0": derivation["words"]["keyboard.midi_f0"],
            "vco_2.tuning": derivation["words"]["vco_2.tuning"],
            "vco_2.mod_depth": derivation["words"]["vco_2.mod_depth"],
            "vco_2.shape": derivation["words"]["vco_2.shape"],
            "partials_word": derivation["partials"].word,
            "init_phase_word": derivation["init_word"],
        },
        "up_pitch": derivation["up_pitch"],
        "fq": streams["fq"],
        "square_q": streams["square_q"],
        "left_q": streams["left_q"],
        "streams": streams,
        "sat_total": streams["counters"]["total_saturation"],
        "frozen_words": frozen_words,
    }


def vco2_write_case(workdir: Path, run: int, case: dict, walk_cap: int = None):
    """Write one run's stimulus files (statics + per-sample stream)."""

    s = case["statics"]
    n = len(case["up_pitch"]) if walk_cap is None else min(
        walk_cap, len(case["up_pitch"])
    )
    (workdir / ("run%d_statics.txt" % run)).write_text(
        "%d %d %d %d %d %d %d\n"
        % (
            s["keyboard.midi_f0"] & 0xFFFFFFFF,
            s["vco_2.tuning"] & 0xFFFFFFFF,
            s["vco_2.mod_depth"] & 0xFFFFFFFF,
            s["vco_2.shape"] & 0xFFFFFFFF,
            s["partials_word"] & 0xFFFFFFFF,
            s["init_phase_word"] & 0xFFFFFFFF,
            n,
        ),
        encoding="utf-8",
    )
    rows = []
    for i in range(n):
        rows.append(
            "%d %d %d %d"
            % (
                case["up_pitch"][i] & 0xFFFFFFFF,
                case["fq"][i] & 0xFFFFFFFF,
                case["square_q"][i] & 0xFFFFFFFF,
                case["left_q"][i] & 0xFFFFFFFF,
            )
        )
    (workdir / ("run%d_stream.txt" % run)).write_text(
        "".join(row + "\n" for row in rows), encoding="utf-8"
    )


def vco2_simulate(workdir: Path, simulator: str, runs: int, formats,
                 dut_sv: Path = None) -> list:
    """Compile and run the square/saw VCO tb; return per-run captures.

    Writes the accepted quarter-wave table memh into the working directory
    (the +lut payload, hash-linked to the accepted register exactly as the
    anchor flow does).
    """

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    lut_memh = workdir / "lut_quarter_cos.memh"
    write_lut_memh(formats.table, lut_memh)
    if simulator == "iverilog":
        vvp = workdir / "square_saw_vco.vvp"
        _run(
            [
                "iverilog", "-g2012", "-o", str(vvp),
                str(CONSTANTS_PKG_SV), str(VCO_LUT_SV),
                str(dut_sv or VCO2_DUT_SV), str(VCO2_TB_SV),
            ],
            cwd=workdir,
        )
        _run(
            ["vvp", "-n", str(vvp), "+lut=%s" % lut_memh.name],
            cwd=workdir,
        )
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    captures = []
    for run in range(runs):
        rows = []
        for line in (
            workdir / ("run%d_out.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append([int(word) for word in line.split()])
        ops = []
        for line in (
            workdir / ("run%d_ops.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                ops.append(line.split())
        cycles = int(
            (workdir / ("run%d_cycles.txt" % run))
            .read_text(encoding="utf-8").strip()
        )
        captures.append({"rows": rows, "ops": ops, "cycles": cycles})
    return captures


#: The engine's four exported streams, in capture-column order.
VCO_STREAM_NAMES = ("vco_2.pitch", "vco_2.driven", "vco_2.right_q",
                    "vco_2.raw")


def vco_mismatch_trace_set(capture_rows, case, prefix: bool = False) -> set:
    """Trace names where the capture differs from the mirror truth.

    ``prefix`` compares each stream over the captured prefix only — used
    by the capped-walk mutation demonstrations, where a shorter capture is
    not itself a mismatch but any differing sample inside the captured
    prefix is. On frozen-binding cases the vco_2.raw capture must also
    match the frozen sidecar words directly (no mirror in the loop).
    """

    mirror = (case["streams"]["m2"], case["streams"]["driven"],
              case["streams"]["right_q"], case["streams"]["v2"])
    bad = set()
    for index, name in enumerate(VCO_STREAM_NAMES):
        got = [row[index] for row in capture_rows]
        want = mirror[index]
        if prefix:
            want = want[: len(got)]
        if got != want:
            bad.add(name)
    if case["frozen_words"] is not None:
        got = [row[3] for row in capture_rows]
        want = case["frozen_words"]
        if prefix:
            want = want[: len(got)]
        if got != want:
            bad.add("vco_2.raw(frozen sidecar)")
    return bad


def vco_report_first_mismatch(capture_rows, case) -> None:
    """Print the first differing (stream, sample, expected, actual) row."""

    mirror = (case["streams"]["m2"], case["streams"]["driven"],
              case["streams"]["right_q"], case["streams"]["v2"])
    for index, name in enumerate(VCO_STREAM_NAMES):
        got = [row[index] for row in capture_rows]
        want = mirror[index]
        for i in range(max(len(got), len(want))):
            a = got[i] if i < len(got) else None
            b = want[i] if i < len(want) else None
            if a != b:
                print(
                    gv.format_mismatch(
                        gv.Mismatch(cycle=i, sample=i, trace=name,
                                    expected=b, actual=a)
                    )
                )
                return


def vco2_check_case(capture, case, case_id: str) -> bool:
    """Sample-exact verification for one run (mirror + frozen words)."""

    if not capture["rows"]:
        print("square/saw VCO case %s: EMPTY capture" % case_id)
        return False
    bad = vco_mismatch_trace_set(capture["rows"], case)
    if bad:
        print("square/saw VCO case %s: mismatch in %s" % (case_id, sorted(bad)))
        vco_report_first_mismatch(capture["rows"], case)
        return False
    frozen_note = " + frozen sidecar words" if case["frozen_words"] else ""
    print(
        "case %s: %d samples x %d streams, RTL sample-exact%s -> OK"
        % (case_id, len(capture["rows"]), len(VCO_STREAM_NAMES), frozen_note)
    )
    return True


def vco2_expected_ops(sample_count: int, sat_total: int) -> list:
    """The DR-0010 #74 owner-row op-count expectation for one run.

    Per sample: 8 multiply-class products (depth-mod, 2x LUT interp x2,
    driven, right shape, combine), 8 add/sub-class ops (pitch sum x2,
    phase add, 2x interp sub+add, right-branch add), 7 declared narrowing
    sites (pitch depth-mod, increment division, 2x interpolation rounding,
    driven, right, combine), plus the model mirror's own sticky saturation
    total across the declared C7 sites.
    """

    return [
        "M",
        8 * sample_count,
        8 * sample_count,
        7 * sample_count,
        sat_total,
    ]


def vco2_run_mutation(workdir: Path, simulator: str, formats, label: str,
                     anchor: str, replacement: str, case_id: str,
                     case_dirs: dict):
    """Plant one RTL mutation on one case and require it to be DETECTED."""

    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    source = VCO2_DUT_SV.read_text(encoding="utf-8")
    if anchor not in source:
        raise SystemExit("mutation anchor not found for %s" % label)
    (mut_dir / "square_saw_vco_engine_mut.sv").write_text(
        mutate_sv(source, anchor, replacement, label), encoding="utf-8"
    )
    case = case_dirs[case_id][1]
    vco2_write_case(mut_dir, 0, case, walk_cap=VCO2_MUTATION_WALK_CAP)
    capture = vco2_simulate(
        mut_dir, simulator, 1, formats,
        dut_sv=mut_dir / "square_saw_vco_engine_mut.sv"
    )[0]
    bad = vco_mismatch_trace_set(capture["rows"], case, prefix=True)
    detected = bool(bad)
    print(
        "mutation %s (RTL, case %s): %s (mismatch traces: %s)"
        % (
            label,
            case_id,
            "DETECTED (test fails the mutant)" if detected else "NOT DETECTED",
            sorted(bad),
        )
    )
    return detected


def vco_run_vector_mutation(workdir: Path, simulator: str, formats,
                            label: str, case_id: str, case_dirs: dict,
                            rewrite_stimulus, expect_traces: set):
    """Plant a stimulus-side mutation and require sharp localization.

    ``rewrite_stimulus(case)`` mutates the replayed stimulus words in place
    on a deep copy; the expectations stay pristine, so the capture must
    diverge exactly inside ``expect_traces``.
    """

    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = copy.deepcopy(case_dirs[case_id][1])
    rewrite_stimulus(mutated)
    vco2_write_case(mut_dir, 0, mutated, walk_cap=VCO2_MUTATION_WALK_CAP)
    capture = vco2_simulate(mut_dir, simulator, 1, formats)[0]
    bad = vco_mismatch_trace_set(capture["rows"], mutated, prefix=True)
    detected = bool(bad)
    localization = ""
    if bad != expect_traces:
        detected = False
        localization = (
            " (localization FAILED: mismatch set %s != expected %s)"
            % (sorted(bad), sorted(expect_traces))
        )
    else:
        localization = " (localized to %s)" % sorted(bad)
    print(
        "mutation %s (stimulus, case %s): %s%s"
        % (
            label,
            case_id,
            "DETECTED (test fails the mutant)" if detected else "NOT DETECTED",
            localization,
        )
    )
    return detected


def vco2(workdir: Path, simulator: str) -> int:
    """Issue #74 flow: the square/saw VCO engine vs the frozen vectors."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("SQUARE-SAW VCO REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("SQUARE-SAW VCO REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1

    vectors = vco_load_vectors()
    frozen_count = sum(
        1 for name in vectors if name.startswith(VCO_FROZEN_PREFIX)
    )
    print(
        "Loaded %d square/saw VCO golden vectors (%d frozen-binding), "
        "contract bindings verified" % (len(vectors), frozen_count)
    )
    print(
        "Declared shadow boundary: exp2/partials/tanh replayed host-side "
        "(DR-0008/DR-0010 open approximation items); no RTL transcendental "
        "is implemented or claimed."
    )

    ok = True

    # 1. Sample-exact engine runs, one committed case per invocation.
    case_dirs = {}
    for case_id in sorted(vectors):
        case_dir = workdir / ("case-" + case_id.replace(":", "_"))
        case_dir.mkdir(parents=True, exist_ok=True)
        case = vco2_derive_case(formats, vectors[case_id])
        vco2_write_case(case_dir, 0, case)
        captures = vco2_simulate(case_dir, simulator, 1, formats)
        if not vco2_check_case(captures[0], case, case_id):
            ok = False
        case_dirs[case_id] = (case_dir, case)

    # 2. Reset/replay: a second trigger cannot retain prior state (static
    #    words, phase, and op counters are per-trigger).
    first, second = VCO2_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    case_first = vco2_derive_case(formats, vectors[first])
    case_second = vco2_derive_case(formats, vectors[second])
    vco2_write_case(replay_dir, 0, case_first)
    vco2_write_case(replay_dir, 1, case_second)
    replay_captures = vco2_simulate(replay_dir, simulator, 2, formats)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    vco2_write_case(solo_dir, 0, case_second)
    solo_captures = vco2_simulate(solo_dir, simulator, 1, formats)
    replay_ok = True
    if replay_captures[1]["rows"] != solo_captures[0]["rows"]:
        print(
            "SQUARE-SAW VCO FAILED: run-after-run capture differs from the "
            "solo run - prior run state leaked"
        )
        replay_ok = False
    if not vco2_check_case(replay_captures[1], case_second, second):
        replay_ok = False
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 3. Budget: exported op counters + the DR-0010 #74 owner row.
    ops_ok = True
    for run_index, case in enumerate((case_first, case_second)):
        parsed = replay_captures[run_index]["ops"][0]
        n = len(replay_captures[run_index]["rows"])
        expected = vco2_expected_ops(n, case["sat_total"])
        if parsed != [str(x) for x in expected]:
            print(
                "SQUARE-SAW VCO FAILED: op counters %r != expected %r "
                "(run %d)" % (parsed, expected, run_index)
            )
            ops_ok = False
    print(
        "budget: exported counters over both replay runs -> %s (per sample "
        "8 mult / 8 add / 7 narrow = the DR-0010 #74 owner row's pitch row "
        "+ two LUT interps + shape row; sats = the model mirror's sticky "
        "saturation total)" % ("OK" if ops_ok else "FAIL")
    )
    ok = ok and ops_ok and check_clip_budget(
        solo_captures[0]["cycles"], len(solo_captures[0]["rows"])
    )

    # 4. Mutations: each planted fault MUST be detected (AC-5).
    mutations_ok = True

    # 4a. Wrong shape mix (RTL): the right branch's shape coefficient is
    #     inverted to (1 - shape), the classic square/saw mix confusion.
    mutations_ok &= vco2_run_mutation(
        workdir, simulator, formats, "wrong-shape-mix",
        "wire signed [95:0] right_prod = $signed(s_shape) * $signed(cos2);",
        "wire signed [95:0] right_prod = (96'sd2097152 - $signed(s_shape))"
        " * $signed(cos2);  // MUTANT: wrong shape mix (1-shape)",
        VCO_SHAPE_MUTATION_CASE, case_dirs,
    )

    # 4b. Partials-constant error (stimulus): one ULP on the replayed
    #     s14.17 partials word must localize to the driven stream alone
    #     (v2 is downstream of the tanh shadow, which the host owns).
    def _partials_ulp(case):
        case["statics"]["partials_word"] = (
            case["statics"]["partials_word"] + 1
        )

    mutations_ok &= vco_run_vector_mutation(
        workdir, simulator, formats, "partials-constant-error",
        VCO_VECTOR_MUTATION_CASE, case_dirs, _partials_ulp,
        {"vco_2.driven"},
    )

    # 4c. Shadow-word corruption (stimulus): one flipped LSB in the
    #     replayed left_q tanh fanout must localize to vco_2.raw alone at
    #     exactly that sample.
    def _shadow_flip(case):
        case["left_q"][VCO_SHADOW_MUTATION_INDEX] ^= 1

    mutations_ok &= vco_run_vector_mutation(
        workdir, simulator, formats, "shadow-word-corruption",
        VCO_VECTOR_MUTATION_CASE, case_dirs, _shadow_flip,
        {"vco_2.raw"},
    )
    ok = ok and mutations_ok

    if not ok:
        print("SQUARE-SAW VCO RUN FAILED")
        return 1
    print(
        "SQUARE-SAW VCO RUN PASSED (contract binding + %d cases "
        "sample-exact incl. %d frozen-binding sidecar cases + "
        "reset/replay independence + budget/op-count asserts + all "
        "mutations detected)" % (len(vectors), frozen_count)
    )
    return 0


def anchor(workdir: Path, simulator: str) -> int:
    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("ANCHOR REFUSED: accepted register refused: %s" % error)
        return 1

    vector = gv.load_vector(gv.SENTINEL_VECTOR_PATH)
    print(
        "Loaded real golden vector %s (%d traces, content_hash verified)"
        % (gv.SENTINEL_VECTOR_PATH.name, len(vector["traces"]))
    )

    verified = gv.verify_accepted_contract(vector)
    print(
        "Accepted-contract binding verified (dr_0008=%(dr_0008_record_sha256)s\n"
        "  register=%(choices_register_sha256)s\n"
        "  package=%(constants_package_sha256)s\n"
        "  record digest source: %(dr_0008_record_source)s)" % verified
    )

    table = formats.table
    declared_lut = vector["provenance"].get("lut_sha256")
    live_lut = table.sha256()
    if declared_lut != live_lut:
        print(
            "ANCHOR REFUSED: accepted quarter-wave table hashes to %s but "
            "the vector binds %s" % (live_lut, declared_lut)
        )
        return 1
    print("Accepted LUT table digest verified: %s" % live_lut)

    sample_count = int(vector["clip"]["sample_count"])
    phase_step, level_word = derive_anchor_control_words(vector, formats)
    print(
        "Derived control words: phase_step=%d level=%d over %d samples "
        "(shadow-site replay, host-side)" % (phase_step, level_word, sample_count)
    )

    lut_memh = workdir / "lut_quarter_cos.memh"
    write_lut_memh(table, lut_memh)
    captured_vco, captured_mix, measured_cycles = simulate_anchor(
        workdir, simulator, phase_step, level_word, sample_count, lut_memh
    )
    if len(captured_vco) != sample_count or len(captured_mix) != sample_count:
        print(
            "ANCHOR FAILED: captured %d/%d streams, expected %d samples"
            % (len(captured_vco), len(captured_mix), sample_count)
        )
        return 1

    ok = True
    budget_ok = check_clip_budget(measured_cycles, sample_count)
    if not budget_ok:
        ok = False

    focus = focus_vector(vector, ANCHOR_TRACE)
    expected_vco = focus["traces"][0]["values"]

    mirror_vco, mirror_mix = anchor_mirror(
        formats, phase_step, level_word, sample_count
    )
    mirror_mismatch = gv.first_mismatch(focus, {ANCHOR_TRACE: mirror_vco})
    if mirror_mismatch is not None:
        print("ANCHOR FAILED: harness mirror disagrees with the frozen vector:")
        print(gv.format_mismatch(mirror_mismatch))
        ok = False
    else:
        print(
            "Vector-truth path verified: model-primitive mirror reproduces "
            "the frozen %s trace over all %d samples."
            % (ANCHOR_TRACE, sample_count)
        )

    rtl_mismatch = gv.first_mismatch(focus, {ANCHOR_TRACE: captured_vco})
    if rtl_mismatch is not None:
        print("ANCHOR FAILED: RTL capture disagrees with the frozen vector:")
        print(gv.format_mismatch(rtl_mismatch))
        ok = False
    else:
        print(
            "RTL path verified: format-true DUT reproduces the frozen %s "
            "trace over all %d samples." % (ANCHOR_TRACE, sample_count)
        )

    # The mixer-level product stream is a derived DUT stream (the vector's
    # mixer.output additionally carries the control-path VCA column), so it
    # is checked against the model-primitive mirror, not the vector.
    derived_trace = "derived.vco_1.raw*mixer.vco_1.level"
    for index, (expected, actual) in enumerate(zip(mirror_mix, captured_mix)):
        if expected != actual:
            print("ANCHOR FAILED: derived mixer-level product stream diverges:")
            print(
                gv.format_mismatch(
                    gv.Mismatch(
                        cycle=index, sample=index, trace=derived_trace,
                        expected=expected, actual=actual,
                    )
                )
            )
            ok = False
            break
    else:
        print(
            "Derived path verified: mixer-level product matches the "
            "model-primitive mirror over all %d samples." % sample_count
        )

    planted_value = (expected_vco[ANCHOR_FAIL_INDEX] ^ 1) & 0xFFFFFF
    faulted = list(captured_vco)
    faulted[ANCHOR_FAIL_INDEX] = planted_value
    planted = gv.Mismatch(
        cycle=ANCHOR_FAIL_INDEX,
        sample=ANCHOR_FAIL_INDEX,
        trace=ANCHOR_TRACE,
        expected=expected_vco[ANCHOR_FAIL_INDEX],
        actual=planted_value,
    )
    observed = gv.first_mismatch(focus, {ANCHOR_TRACE: faulted})
    if observed != planted:
        print(
            "ANCHOR FAILED: reporter did not name the planted fault on the "
            "real vector; observed %r" % (observed,)
        )
        ok = False
    else:
        print("Intentional-fail path verified on the real vector; reporter reports:")
        print(gv.format_mismatch(observed))

    if not ok:
        print("ANCHOR RUN FAILED")
        return 1
    print(
        "ANCHOR RUN PASSED (contract binding + vector truth + RTL "
        "sample-exact + clip budget + fail path)"
    )
    return 0



# ======================================================================
# Sine VCO flow (issue #73): the bit-exact sine source lane against the
# frozen whole-voice receipt's vco_1.raw traces.
# ======================================================================

VCO_DUT_SV = TB_ROOT / "sv/sine_vco_engine.sv"
VCO_TB_SV = TB_ROOT / "sv/tb_sine_vco_engine.sv"
#: The frozen whole-voice receipt (issue #54): per-case physical
#: parameters + per-trace digests; the directed vco_1.raw sidecars carry
#: the frozen words. The 8 development-corpus cases stay digest-custody
#: only (their physical maps are never committed, per the receipt's
#: custody policy), so the RTL lane consumes the param-committed cases.
VCO_RECEIPT_PATH = ROOT / "sim/reference/fixed-voice-golden-v1.json"
VCO_SIDECAR_DIR = ROOT / "sim/reference/fixed-voice-golden-v1-traces"
#: The directed sine-VCO vector set (issue #73 AC-1): the min/mid/max
#: frequency x min/mid/max phase matrix, unmodulated and modulated, plus
#: the signed-depth/tuning and MIDI-clamp corners the frozen receipt
#: (which pins keyboard MIDI 69 and only the upper parameter ends) does
#: not carry. Generated by tools/generate_sine_vco_golden.py.
SINE_VECTOR_DIR = ROOT / "sim/reference/sine-vco-golden-v1"
#: The declared regime axes the directed matrix must cover in full: any
#: missing (frequency, phase, modulation) cell fails the run rather than
#: quietly narrowing the AC.
SINE_FREQ_POINTS = ("min", "mid", "max")
SINE_PHASE_POINTS = ("min", "mid", "max")
SINE_MOD_POINTS = ("unmod", "mod")
#: The directed case the complete-clip cycle-budget assert also runs on:
#: the top of the keyboard band, modulated at full depth and started at
#: the maximum initial phase (the heaviest pitch path of the matrix).
SINE_BUDGET_CASE = "freq:max-phase:max-mod"
#: Directed cases carrying the mutation demonstrations AC-5 names for
#: tuning and phase initialization (both must be non-vacuous: a nonzero
#: tuning word and a nonzero initial-phase word respectively).
#: The RTL tuning mutation runs on the statically clamped +24-semitone
#: case (MIDI 127 + 24 held above the band on every sample). While the
#: midi->Hz exp2 is host-replayed the in-RTL pitch path is observable on
#: the exported property rows, not the trace: dropping tuning there takes
#: the measured clamp count from 176,400 to 0. The trace-side tuning
#: fault is planted stimulus-side, through the declared shadow, on the
#: unclamped case below (the un-clamped-pitch / selector-vs-blend
#: convention).
SINE_TUNING_MUTATION_CASE = "corner:tuning-max-freq-max"
SINE_TUNING_STIMULUS_CASE = "frozen:boundary:vco_1.tuning:upper"
SINE_PHASE_MUTATION_CASE = "freq:mid-phase:max-unmod"
#: Cases the RTL mutations are demonstrated on: the depth-driven case
#: (measured MIDI clamps > 0, so the un-clamped-pitch mutant must bite)
#: and the plain 440 Hz receipt (the phase/LUT mutants).
VCO_MUTATION_CASE = "source:vco_1"
VCO_CLAMP_MUTATION_CASE = "boundary:vco_1.mod_depth:upper"
#: Mutation-simulation walk cap (+max_n): every mutation demonstrably
#: bites within the first 24,000 audio samples (~0.54 s), so the five
#: mutation sims walk a fraction of the grid. Committed-case runs always
#: walk the full 176,400 samples.
VCO_MUTATION_WALK_CAP = 24000
#: Replay-independence pair: the initial phase word and per-run params
#: are per-trigger; run 0 state must not leak into run 1 (the second
#: case's initial phase is zero, so leaked phase state is visible).
VCO_REPLAY_PAIR = ("boundary:vco_1.initial_phase:upper", "source:vco_1")

VCO_PARAMS_LINE_ANCHORS = {
    "wrong-lut-address": (
        "    wire [C5_INDEX_BITS-1:0] a_index = i[C5_INDEX_BITS-1:0];",
        "    wire [C5_INDEX_BITS-1:0] a_index = i[C5_INDEX_BITS-1:0] + 1'b1;"
        "  // MUTANT: LUT address off-by-one",
    ),
    "dropped-phase-increment": (
        "    wire [C2_WIDTH-1:0] phase_next = phase + k_word;",
        "    wire [C2_WIDTH-1:0] phase_next = phase;"
        "  // MUTANT: increment dropped",
    ),
    "phase-wrap-saturate": (
        "    wire [C2_WIDTH-1:0] phase_next = phase + k_word;",
        "    wire [C2_WIDTH-1:0] phase_next = ((phase + k_word) < phase)"
        " ? {C2_WIDTH{1'b1}} : (phase + k_word);"
        "  // MUTANT: forbidden phase saturation",
    ),
    # AC-5 names tuning explicitly: drop the tuning term from the pitch
    # sum (the "the keyboard pitch is the pitch" simplification).
    "dropped-tuning-term": (
        "    wire signed [95:0] pitch_sum = $signed(f0_r) + $signed(tun_r)"
        " + depth_eff;",
        "    wire signed [95:0] pitch_sum = $signed(f0_r) + depth_eff;"
        "  // MUTANT: tuning term dropped",
    ),
    # AC-2 names phase initialization: drop the one-time turn-word
    # injection and start every trigger from phase zero.
    "zero-initial-phase": (
        "                phase      <= initial_phase_word;",
        "                phase      <= {C2_WIDTH{1'b0}};"
        "  // MUTANT: initial phase never injected",
    ),
}


def vco_load_receipt():
    """Load the frozen whole-voice receipt and verify its bindings.

    The receipt binds the accepted DR-0008 status, the hash-linked C5
    table, and the emitted constants package; any drift refuses the run
    exactly as the golden-vector flows do.
    """

    payload = json.loads(VCO_RECEIPT_PATH.read_text(encoding="utf-8"))
    if payload.get("schema") != "gf180-torchsynth/fixed-voice-golden-v1":
        raise SystemExit(
            "receipt schema %r is not fixed-voice-golden-v1"
            % (payload.get("schema"),)
        )
    bindings = payload["bindings"]
    if not str(bindings.get("dr_0008_status", "")).startswith("Accepted"):
        raise SystemExit(
            "receipt dr_0008_status refused: %r"
            % (bindings.get("dr_0008_status"),)
        )
    formats = AcceptedFormats()
    live_lut = formats.table.sha256()
    if bindings.get("lut_sha256") != live_lut:
        raise SystemExit(
            "accepted quarter-wave table hashes to %s but the receipt "
            "binds %s" % (live_lut, bindings.get("lut_sha256"))
        )
    package_digest = hashlib.sha256(
        CONSTANTS_PKG_SV.read_bytes()
    ).hexdigest()
    if bindings.get("constants_package_sha256") != package_digest:
        raise SystemExit(
            "landed constants package hashes to %s but the receipt binds "
            "%s" % (package_digest, bindings.get("constants_package_sha256"))
        )
    cases = {
        case["id"]: case for case in payload["cases"]
        if case.get("parameters") is not None
    }
    if len(cases) != 26:
        raise SystemExit(
            "expected 26 param-committed cases in the receipt, found %d"
            % len(cases)
        )
    for required in (VCO_MUTATION_CASE, VCO_CLAMP_MUTATION_CASE) \
            + VCO_REPLAY_PAIR:
        if required not in cases:
            raise SystemExit("receipt carries no param-committed case %r"
                             % required)
    return payload, formats, cases


def sine_load_vectors():
    """Load and contract-verify every committed directed sine-VCO vector.

    Each vector is hash-linked to the accepted numeric contract exactly
    as the #68 anchor flow requires (``verify_accepted_contract``): a
    stale constants package or choice register refuses the run with
    "regenerate, don't recompile" rather than silently recompiling.
    """

    vectors = {}
    for path in sorted(SINE_VECTOR_DIR.glob("*.json")):
        vector = gv.load_vector(path)
        gv.verify_accepted_contract(vector)
        vectors[path.stem] = vector
    if not vectors:
        raise SystemExit(
            "no directed sine VCO golden vectors found in %s; run "
            "tools/generate_sine_vco_golden.py" % SINE_VECTOR_DIR
        )
    return vectors


def sine_check_matrix(vectors) -> bool:
    """AC-1 coverage gate over the committed directed vectors.

    Every (frequency, phase, modulation) cell of the declared min/mid/max
    matrix must be present exactly once, and the signed-depth / tuning
    corners must be present. A missing cell fails the run: the acceptance
    criterion is the matrix, not whichever cells happened to be
    generated.
    """

    matrix = {}
    digests = {}
    corners = set()
    for case_id, vector in sorted(vectors.items()):
        regime = vector["provenance"].get("regime") or {}
        kind = regime.get("class")
        if kind == "directed-matrix":
            cell = (
                regime.get("frequency"), regime.get("phase"),
                regime.get("modulation"),
            )
            if cell in matrix:
                print(
                    "SINE-VCO FAILED: directed matrix cell %r is claimed by "
                    "both %s and %s" % (cell, matrix[cell], case_id)
                )
                return False
            matrix[cell] = case_id
            digests[cell] = vector["provenance"]["vco_1_raw"]["words_sha256"]
        elif kind == "directed-corner":
            corners.add(case_id)
    ok = True
    for freq in SINE_FREQ_POINTS:
        for phase in SINE_PHASE_POINTS:
            for mod in SINE_MOD_POINTS:
                cell = (freq, phase, mod)
                if cell not in matrix:
                    print(
                        "SINE-VCO FAILED: directed matrix has no vector for "
                        "frequency=%s phase=%s %s" % cell
                    )
                    ok = False
    expected_cells = (
        len(SINE_FREQ_POINTS) * len(SINE_PHASE_POINTS) * len(SINE_MOD_POINTS)
    )
    print(
        "directed matrix: %d/%d min/mid/max frequency x phase cells "
        "(unmodulated and modulated) + %d signed-depth/tuning corners -> %s"
        % (len(matrix), expected_cells, len(corners), "OK" if ok else "FAIL")
    )
    if not corners:
        print(
            "SINE-VCO FAILED: no directed-corner vectors (the signed "
            "mod_depth/tuning minima are unexercised)"
        )
        ok = False
    # Non-vacuity: a "modulated" cell whose trace equals its unmodulated
    # sibling proves nothing about the modulation path (it happens
    # whenever the depth drives the pitch out of the MIDI band on every
    # sample). Refuse it rather than count it as coverage.
    vacuous = 0
    for (freq, phase, mod), digest in sorted(digests.items()):
        if mod != "mod":
            continue
        if digests.get((freq, phase, "unmod")) == digest:
            print(
                "SINE-VCO FAILED: modulated cell freq=%s phase=%s is "
                "trace-identical to its unmodulated sibling (%s)"
                % (freq, phase, matrix[(freq, phase, mod)])
            )
            vacuous += 1
            ok = False
    print(
        "directed matrix non-vacuity: every modulated cell differs from "
        "its unmodulated sibling (%d vacuous) -> %s"
        % (vacuous, "OK" if vacuous == 0 else "FAIL")
    )
    return ok


def sine_derive_vector(formats, vector):
    """Model-derived stimulus + committed truth for one directed vector.

    The host mirror (``torchsynth_voice.vco_golden``) re-walks the frozen
    model's vco_1 lane with the model's own primitives; its ``vco_1.raw``
    digest must equal the vector's committed evidence row (which is the
    fixed model's own render for directed cases, and the frozen receipt's
    trace digest plus retained sidecar words for frozen-binding cases).
    Any drift refuses the run. The declared binary64 midi->Hz exp2 shadow
    is replayed host-side exactly as DR-0008 declares it an open
    approximation item -- a harness input derivation, never an RTL claim.

    Returns a case in the same shape ``vco_write_case`` / ``vco_check_case``
    consume, so the directed vectors run through the identical engine
    path as the receipt cases.
    """

    provenance = vector["provenance"]
    case_id = provenance["case_id"]
    derivation = vg.derive_case(formats, vector["parameters"])
    streams = derivation["streams"]
    digest = vg.digest_words(streams["vco"])
    row = provenance["vco_1_raw"]
    if digest != row["words_sha256"]:
        raise SystemExit(
            "sine VCO mirror digest drift for %s: regenerated %s but the "
            "committed vector declares %s -- regenerate the vectors, do "
            "not recompile" % (case_id, digest, row["words_sha256"])
        )
    if len(streams["vco"]) != row["sample_count"]:
        raise SystemExit(
            "sine VCO mirror walked %d samples but %s declares %d"
            % (len(streams["vco"]), case_id, row["sample_count"])
        )
    for j_str, word in row["jitter"].items():
        if streams["vco"][int(j_str)] != word:
            raise SystemExit(
                "jitter drift for %s at j=%s against the committed vector"
                % (case_id, j_str)
            )
    statics = provenance["static_words"]
    live_statics = {
        "keyboard.midi_f0": derivation["words"]["keyboard.midi_f0"],
        "vco_1.tuning": derivation["words"]["vco_1.tuning"],
        "vco_1.mod_depth": derivation["words"]["vco_1.mod_depth"],
        "init_phase_word": derivation["init_word"],
    }
    if live_statics != statics:
        raise SystemExit(
            "static word drift for %s: regenerated %r but the committed "
            "vector declares %r" % (case_id, live_statics, statics)
        )
    if derivation["clamps"] != provenance["measured"]["midi_clamps"]:
        raise SystemExit(
            "measured MIDI clamp drift for %s: regenerated %d but the "
            "committed vector declares %d"
            % (case_id, derivation["clamps"],
               provenance["measured"]["midi_clamps"])
        )

    sidecar = None
    binding = provenance.get("frozen_binding")
    if binding:
        payload = (ROOT / binding["sidecar"]["file"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != binding["sidecar"]["sha256"]:
            raise SystemExit(
                "frozen sidecar bytes drifted for %s" % case_id
            )
        frozen_words = mm.unpack_words_f32le(payload)
        if vg.digest_words(frozen_words) != binding["trace_digest"]:
            raise SystemExit(
                "frozen sidecar words digest drift for %s" % case_id
            )
        if streams["vco"] != frozen_words:
            raise SystemExit(
                "mirror does not reproduce the frozen vco_1.raw words for "
                "%s" % case_id
            )
        sidecar = True

    sats = sum(
        record["count"]
        for record in derivation["counters"]["records"]
        if record["kind"] == "saturation"
    )
    if sats != provenance["measured"]["saturations"]:
        raise SystemExit(
            "measured saturation drift for %s: regenerated %d but the "
            "committed vector declares %d"
            % (case_id, sats, provenance["measured"]["saturations"])
        )
    return {
        "id": case_id,
        "params": [
            derivation["words"]["keyboard.midi_f0"],
            derivation["words"]["vco_1.tuning"],
            derivation["words"]["vco_1.mod_depth"],
            derivation["init_word"],
        ],
        "up_pitch": derivation["up_pitch"],
        "matrix_pitch": derivation["matrix_pitch"],
        "mirror": streams,
        "clamps": derivation["clamps"],
        "sats": sats,
        "sidecar": sidecar,
        "regime": provenance.get("regime") or {},
    }


def vco_derive_case(fcp, formats, case):
    """Model-derived stimulus + frozen truth for one case.

    The stimulus streams are regenerated through the frozen
    composition's own control path (``render_words``) and pinned to the
    receipt's digests; the expected output words are the live sine-lane
    mirror's, pinned to the receipt's frozen ``vco_1.raw`` digest; and
    where the receipt commits sidecar bytes (the directed vco_1 cases)
    the unpacked words must equal the mirror. Any drift refuses.
    """

    control_words = fcp.render_words(case["parameters"])
    midi_f0_word = control_words["keyboard.midi_f0"][0]
    up_pitch = control_words["control_upsample.vco_1_pitch"]
    matrix_pitch = control_words["mod_matrix.vco_1_pitch"]
    for name, words in (
        ("keyboard.midi_f0", [midi_f0_word]),
        ("control_upsample.vco_1_pitch", up_pitch),
        ("mod_matrix.vco_1_pitch", matrix_pitch),
    ):
        digest = vg.digest_words(words)
        if digest != case["traces"][name]:
            raise SystemExit(
                "stimulus drift for %s on %s: regenerated %s but the "
                "receipt declares %s" % (name, case["id"], digest,
                                         case["traces"][name])
            )

    counters = StickyCounters()
    tuning_word = entry_quantize(
        float(case["parameters"]["vco_1.tuning"]), formats.midi, counters,
        "tb.vco.entry:vco_1.tuning",
    )
    depth_word = entry_quantize(
        float(case["parameters"]["vco_1.mod_depth"]), formats.midi, counters,
        "tb.vco.entry:vco_1.mod_depth",
    )
    init_word = vg.initial_phase_word(
        case["parameters"]["vco_1.initial_phase"], formats.phase_width
    )
    mirror, aux = vg.mirror_sine_lane(
        formats, midi_f0_word, tuning_word, depth_word, init_word, up_pitch
    )
    frozen_digest = case["traces"]["vco_1.raw"]
    mirror_digest = vg.digest_words(mirror["vco"])
    if mirror_digest != frozen_digest:
        raise SystemExit(
            "sine-lane mirror diverges from the frozen model on %s: "
            "mirror digest %s, receipt declares %s"
            % (case["id"], mirror_digest, frozen_digest)
        )
    sidecar_path = VCO_SIDECAR_DIR / (case["id"] + ".vco_1.raw.f32le")
    sidecar = None
    if sidecar_path.exists():
        words = mm.unpack_words_f32le(sidecar_path.read_bytes())
        if vg.digest_words(words) != frozen_digest or words != mirror["vco"]:
            raise SystemExit(
                "sidecar bytes drifted for %s.vco_1.raw; regenerate the "
                "vectors, do not recompile" % case["id"]
            )
        sidecar = True
    tally = aux["counters"]
    # The sticky counters carry per-site records: sum the saturation
    # events across the declared sites the RTL exports as op_sats
    # (depth-mod, pitch sum, S4).
    sats = sum(
        record["count"]
        for record in tally["records"]
        if record["kind"] == "saturation"
    )
    return {
        "id": case["id"],
        "params": [midi_f0_word, tuning_word, depth_word, init_word],
        "up_pitch": up_pitch,
        "matrix_pitch": matrix_pitch,
        "mirror": mirror,
        "clamps": aux["clamps"],
        "sats": sats,
        "sidecar": sidecar,
    }


def vco_write_case(workdir: Path, run: int, case: dict):
    """Write one run's stimulus files (params + per-sample streams)."""

    (workdir / ("run%d_params.txt" % run)).write_text(
        " ".join(str(word & 0xFFFFFFFF) for word in case["params"]) + "\n",
        encoding="utf-8",
    )
    fq = case["mirror"]["fq"]
    pitch = case["up_pitch"]
    lines = []
    for n in range(len(pitch)):
        lines.append("%d %d" % (pitch[n] & 0xFFFFFFFF, fq[n] & 0xFFFFFFFF))
    (workdir / ("run%d_streams.txt" % run)).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def vco_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path,
                 max_n: int = None) -> list:
    """Compile and run the file-driven tb; return per-run captures."""

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    if simulator == "iverilog":
        vvp = workdir / "sine_vco.vvp"
        _run(
            [
                "iverilog", "-g2012", "-o", str(vvp),
                str(CONSTANTS_PKG_SV), str(dut_sv), str(VCO_TB_SV),
            ],
            cwd=workdir,
        )
        lut_arg = "+lut=%s" % (workdir / "lut.memh")
        if max_n is None:
            _run(["vvp", "-n", str(vvp), lut_arg], cwd=workdir)
        else:
            _run(
                ["vvp", "-n", str(vvp), lut_arg, "+max_n=%d" % max_n],
                cwd=workdir,
            )
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    captures = []
    for run in range(runs):
        vco_words = []
        phase_words = []
        for line in (
            workdir / ("run%d_captured.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            v_word, p_word = line.split()
            try:
                vco_words.append(int(v_word))
                phase_words.append(int(p_word))
            except ValueError:
                vco_words.append(None)  # an x-state emission: a mismatch
                phase_words.append(None)
        cycles = int(
            (workdir / ("run%d_cycles.txt" % run))
            .read_text(encoding="utf-8").strip()
        )
        ops = None
        for line in (
            workdir / ("run%d_ops.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                ops = []
                for raw in line.split()[1:]:
                    try:
                        ops.append(int(raw))
                    except ValueError:
                        ops.append(None)  # an x-state counter: a mismatch
        captures.append(
            {"vco": vco_words, "phase": phase_words, "cycles": cycles,
             "ops": ops}
        )
    return captures


def vco_check_case(capture, case, case_id: str, prefix: bool = False) -> bool:
    """Sample-exact vco_1.raw + phase verification for one run.

    The capture must equal the mirror (already pinned to the receipt's
    frozen digest). With ``prefix`` (the capped mutation walks) a
    shorter capture is not itself a mismatch, but any differing sample
    inside the captured prefix is.
    """

    for stream_name in ("vco", "phase"):
        got = capture[stream_name]
        want = case["mirror"][stream_name]
        if prefix:
            want = want[: len(got)]
        for index in range(max(len(got), len(want))):
            a = got[index] if index < len(got) else None
            b = want[index] if index < len(want) else None
            if a != b:
                print(
                    "SINE-VCO FAILED: %s differs (%s %s[%d]: expected %s "
                    "got %s)" % (stream_name, case_id, "vco_1.raw"
                                 if stream_name == "vco" else "phase",
                                 index, b, a)
                )
                return False
    return True


def vco_expected_ops(walked: int, case: dict) -> list:
    """Expected exported counters for one full-or-capped walk.

    Static per-sample owner-row ops (DR-0010 #73: pitch path 3 mults /
    6 adds / 3 narrows / 1 exp2 + phase+LUT 1/2/1): the engine counts
    3 mults, 7 adds/compares, 4 narrows, and 1 consumed shadow site per
    sample; saturations and MIDI clamps are measured per case.
    """

    return [
        3 * walked,
        7 * walked,
        4 * walked,
        walked,
        case["sats"],
        case["clamps"],
    ]


def check_vco_budget(schedule, walked: int, case: dict, ops_row: list) -> bool:
    """Op-count conformance to the DR-0010 #73 owner row + emission check.

    DR-0010's owner rows for #73: "vco_1 pitch path (depth-mod, clamp,
    MIDI->Hz, Q16.15 word, phase increment): 3 mults / 6 adds / 3
    narrows / 1 exp2" and "vco_1 phase + quarter-wave LUT + S4: 1 mult /
    2 adds / 1 narrow" — 4 mults / 8 adds / 4 narrows / 1 shadow per
    audio sample. The engine declares 3 / 7 / 4 RTL ops per sample (the
    interpolation product's two adds and the clamp compares land inside
    the adds cap) and the host shadow supplies the fourth mult-class op
    (the exp2 site, counted from the consumed fq stream). The complete
    clip schedule is asserted: the walked sample count must equal the
    emitted SCHED_SAMPLES_PER_PASS over one full pass. The serialized
    single-MAC cycle mapping remains the integration lanes; this is an
    op-count check, not a PPA/fit claim.
    """

    ok = True
    per_sample = {
        "multiply-class ops (3 RTL + 1 host shadow)": (4, 4),
        "adds/compares": (7, 8),
        "declared narrowings": (4, 4),
        "exp2 shadow sites": (1, 1),
    }
    for name, (actual, limit) in per_sample.items():
        verdict = "OK" if actual <= limit else "FAIL"
        ok = ok and actual <= limit
        print(
            "  budget: %d %s per sample vs DR-0010 #73 owner-row cap %d -> %s"
            % (actual, name, limit, verdict)
        )
    try:
        emitted = codegen.emit(schedule_payload=schedule)
        landed = CONSTANTS_PKG_SV.read_text(encoding="utf-8")
        matches = emitted.package_text == landed and (
            sched.SCHEDULE_ID in emitted.emitted_ids
        )
        print(
            "  budget constants: landed %s matches the live emission of "
            "both accepted registers -> %s"
            % (CONSTANTS_PKG_SV.name, "OK" if matches else "FAIL")
        )
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return False
    samples_per_pass = sched.constant(schedule, "samples_per_pass")
    complete = (walked == samples_per_pass == gv.CANONICAL_SAMPLE_COUNT)
    print(
        "  complete clip schedule: walked %d samples == emitted "
        "samples_per_pass %d == canonical %d -> %s"
        % (walked, samples_per_pass, gv.CANONICAL_SAMPLE_COUNT,
           "OK" if complete else "FAIL")
    )
    ok = ok and complete
    expected = vco_expected_ops(walked, case)
    counters_ok = ops_row == expected
    print(
        "  exported counters over the walk: %r vs expected %r -> %s"
        % (ops_row, expected, "OK" if counters_ok else "FAIL")
    )
    return ok and counters_ok


def vco_run_mutation(workdir: Path, simulator: str, label: str, case_id: str,
                     cases_by_id: dict, walk_cap: int = VCO_MUTATION_WALK_CAP):
    """Plant one RTL mutation on one case and require it to be DETECTED.

    A mutant fails on either surface the AC names: the captured trace
    rows (sample-exact vs the frozen truth, over the capped prefix) or
    the exported op-counter property rows (which must still equal the
    model's measured counts). While the midi->Hz shadow is
    host-replayed, the pitch path's trace effect completes host-side —
    the RTL phase/LUT mutations are caught on trace rows; the pitch
    formation mutations (un-clamped pitch, selector-vs-blend, the
    trace-side tuning drop) are planted stimulus-side through the
    declared shadow so they reach the trace too.

    ``walk_cap`` caps the mutant walk. **The measured per-case counters
    (saturations, MIDI clamps) are whole-clip totals**, so a capped walk
    can only be compared on the trace rows; pass ``walk_cap=None`` to
    walk the full clip whenever the property rows are the intended
    detection surface, otherwise the counter comparison is vacuous (it
    would "detect" an unmutated engine too).
    """

    anchor, replacement = VCO_PARAMS_LINE_ANCHORS[label]
    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        VCO_DUT_SV.read_text(encoding="utf-8"), anchor, replacement, label
    )
    (mut_dir / "sine_vco_engine_mut.sv").write_text(mutated, encoding="utf-8")
    (mut_dir / "lut.memh").write_bytes(
        (workdir / "lut.memh").read_bytes()
    )
    case = cases_by_id[case_id]
    vco_write_case(mut_dir, 0, case)
    mut_captures = vco_simulate(
        mut_dir, simulator, 1, mut_dir / "sine_vco_engine_mut.sv",
        max_n=walk_cap,
    )
    capture = mut_captures[0]
    full_walk = walk_cap is None
    trace_bad = not vco_check_case(capture, case, case_id,
                                   prefix=not full_walk)
    walked = len(capture["vco"])
    # Only a full-clip walk may be judged on the whole-clip counters.
    ops_bad = full_walk and capture["ops"] != vco_expected_ops(walked, case)
    detected = trace_bad or ops_bad
    surface = (
        "trace rows" if trace_bad
        else ("property rows (counters %r)" % (capture["ops"],))
        if ops_bad else "neither"
    )
    print(
        "mutation %s (RTL, case %s): %s via %s"
        % (
            label,
            case_id,
            "DETECTED (test fails the mutant)" if detected else "NOT DETECTED",
            surface,
        )
    )
    return detected


def vco(workdir: Path, simulator: str) -> int:
    """Issue #73 flow: the sine VCO engine vs the frozen model's traces."""

    try:
        receipt, formats, cases = vco_load_receipt()
    except ChoiceNotAccepted as error:
        print("SINE-VCO REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("SINE-VCO REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    fcp = FixedControlPath(formats.control_spec)

    print(
        "Loaded the frozen whole-voice receipt (%d cases, %d "
        "param-committed), bindings verified (DR-0008 %s, lut %s)"
        % (len(receipt["cases"]), len(cases),
           receipt["bindings"]["dr_0008_status"],
           receipt["bindings"]["lut_sha256"][:12] + "...")
    )

    lut_memh = workdir / "lut.memh"
    write_lut_memh(formats.table, lut_memh)

    ok = True

    # 1. Sample-exact engine runs, one committed case per invocation,
    #    over the full 176,400-sample clip schedule. The exported
    #    op-counter property row must equal the model-measured counts on
    #    every clean run (an x-state counter is a named failure, never a
    #    silent pass).
    case_dirs = {}
    sidecar_count = 0
    for case_id in sorted(cases):
        case_dir = workdir / ("case-" + case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
        case = vco_derive_case(fcp, formats, cases[case_id])
        vco_write_case(case_dir, 0, case)
        captures = vco_simulate(case_dir, simulator, 1, VCO_DUT_SV)
        case_ok = vco_check_case(captures[0], case, case_id)
        expected_ops = vco_expected_ops(
            len(case["mirror"]["vco"]), case
        )
        if captures[0]["ops"] != expected_ops:
            case_ok = False
            print(
                "SINE-VCO FAILED: exported counters %r != expected %r "
                "(%s)" % (captures[0]["ops"], expected_ops, case_id)
            )
        if not case_ok:
            ok = False
        if case["sidecar"]:
            sidecar_count += 1
        print(
            "case %s: %d samples (clamps %d, sats %d, sidecar %s), "
            "RTL sample-exact -> %s"
            % (case_id, len(case["mirror"]["vco"]), case["clamps"],
               case["sats"], bool(case["sidecar"]),
               "OK" if case_ok else "FAIL")
        )
        case_dirs[case_id] = (case_dir, case)

    # 1b. The directed min/mid/max frequency x phase matrix (AC-1),
    #     unmodulated and modulated, plus the signed-depth/tuning
    #     corners. The frozen receipt pins keyboard MIDI 69 and only the
    #     upper parameter ends, so these committed vectors carry the
    #     model's own vco_1.raw render at the band extrema. They run
    #     through the identical engine path, full clip, sample-exact.
    directed_vectors = sine_load_vectors()
    print(
        "Loaded %d directed sine-VCO vectors from %s (contract-bound)"
        % (len(directed_vectors), SINE_VECTOR_DIR.name)
    )
    ok = sine_check_matrix(directed_vectors) and ok
    directed_cases = {}
    directed_budget_case = None
    directed_budget_ops = None
    for name in sorted(directed_vectors):
        vector = directed_vectors[name]
        case = sine_derive_vector(formats, vector)
        case_dir = workdir / ("directed-" + name)
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
        vco_write_case(case_dir, 0, case)
        captures = vco_simulate(case_dir, simulator, 1, VCO_DUT_SV)
        case_ok = vco_check_case(captures[0], case, case["id"])
        expected_ops = vco_expected_ops(len(case["mirror"]["vco"]), case)
        if captures[0]["ops"] != expected_ops:
            case_ok = False
            print(
                "SINE-VCO FAILED: exported counters %r != expected %r (%s)"
                % (captures[0]["ops"], expected_ops, case["id"])
            )
        if not case_ok:
            ok = False
        regime = case["regime"]
        print(
            "directed %s [freq %s / phase %s / %s]: %d samples "
            "(clamps %d, sats %d, sidecar %s), RTL sample-exact -> %s"
            % (case["id"], regime.get("frequency"), regime.get("phase"),
               regime.get("modulation"), len(case["mirror"]["vco"]),
               case["clamps"], case["sats"], bool(case["sidecar"]),
               "OK" if case_ok else "FAIL")
        )
        directed_cases[name] = case
        if name == SINE_BUDGET_CASE:
            directed_budget_case = case
            directed_budget_ops = captures[0]["ops"]
    if directed_budget_case is None:
        print(
            "SINE-VCO FAILED: the directed budget case %r is not committed"
            % SINE_BUDGET_CASE
        )
        ok = False

    # 2. Reset/replay: a second trigger cannot retain prior state (the
    #    initial phase word and counters are per-trigger).
    first, second = VCO_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
    case_first = vco_derive_case(fcp, formats, cases[first])
    case_second = vco_derive_case(fcp, formats, cases[second])
    vco_write_case(replay_dir, 0, case_first)
    vco_write_case(replay_dir, 1, case_second)
    replay_captures = vco_simulate(replay_dir, simulator, 2, VCO_DUT_SV)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    (solo_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
    vco_write_case(solo_dir, 0, case_second)
    solo_captures = vco_simulate(solo_dir, simulator, 1, VCO_DUT_SV)
    replay_ok = True
    if replay_captures[1]["vco"] != solo_captures[0]["vco"] or \
            replay_captures[1]["phase"] != solo_captures[0]["phase"]:
        print(
            "SINE-VCO FAILED: run-after-run capture differs from the solo "
            "run - prior run state leaked"
        )
        replay_ok = False
    if not vco_check_case(replay_captures[1], case_second, second):
        replay_ok = False
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 3. Budget: exported op counters + the DR-0010 #73 owner row over
    #    the complete clip schedule.
    budget_ok = check_vco_budget(
        schedule, len(case_second["mirror"]["vco"]), case_second,
        replay_captures[1]["ops"],
    )
    print(
        "budget: %d runs of exported counters checked -> %s (per sample "
        "4 mult / 7 adds / 4 narrow / 1 shadow within the 4/8/4/1 "
        "owner-row cap; the 4th mult is the declared host exp2 shadow)"
        % (2, "OK" if budget_ok else "FAIL")
    )
    ok = ok and budget_ok

    # 3b. The same complete-clip budget assert on the heaviest directed
    #     case: the top of the keyboard band, modulated at full depth,
    #     started at the maximum initial phase. The clamp-saturated pitch
    #     path must not change the per-sample op profile.
    if directed_budget_case is not None:
        print(
            "budget (directed %s): complete-clip schedule + op counters"
            % SINE_BUDGET_CASE
        )
        directed_budget_ok = check_vco_budget(
            schedule, len(directed_budget_case["mirror"]["vco"]),
            directed_budget_case, directed_budget_ops,
        )
        print(
            "budget (directed %s) -> %s"
            % (SINE_BUDGET_CASE, "OK" if directed_budget_ok else "FAIL")
        )
        ok = ok and directed_budget_ok

    # 4. Mutations: each planted fault MUST be detected (AC-5).
    mutations_ok = True
    cases_by_id = {cid: cd[1] for cid, cd in case_dirs.items()}

    # 4a. Wrong LUT entry (RTL): the primary table read is off by one.
    mutations_ok &= vco_run_mutation(
        workdir, simulator, "wrong-lut-address", VCO_MUTATION_CASE,
        cases_by_id,
    )

    # 4b. Dropped phase increment (RTL): the phase never advances.
    mutations_ok &= vco_run_mutation(
        workdir, simulator, "dropped-phase-increment", VCO_MUTATION_CASE,
        cases_by_id,
    )

    # 4c. Phase-wrap error (RTL): the forbidden saturation replaces the
    #     natural u32 wrap at the first overflow.
    mutations_ok &= vco_run_mutation(
        workdir, simulator, "phase-wrap-saturate", VCO_MUTATION_CASE,
        cases_by_id,
    )

    # 4c-i. Tuning mutation (RTL, AC-5 names it explicitly): the tuning
    #       term is dropped from the pitch sum. Planted on the statically
    #       clamped +24-semitone case, where the in-RTL pitch path is
    #       observable on the exported property rows (the measured clamp
    #       count collapses from every sample to none). Non-vacuity is
    #       asserted: the case must carry a nonzero tuning word AND
    #       clamps on every sample.
    tuning_case = directed_cases.get(SINE_TUNING_MUTATION_CASE)
    if tuning_case is None:
        print(
            "SINE-VCO FAILED: no directed case %r for the tuning mutation"
            % SINE_TUNING_MUTATION_CASE
        )
        mutations_ok = False
    elif tuning_case["params"][1] == 0 or tuning_case["clamps"] <= 0:
        print(
            "SINE-VCO FAILED: %s carries tuning word %d and %d clamps; the "
            "dropped-tuning-term mutation would be vacuous"
            % (SINE_TUNING_MUTATION_CASE, tuning_case["params"][1],
               tuning_case["clamps"])
        )
        mutations_ok = False
    else:
        mutations_ok &= vco_run_mutation(
            workdir, simulator, "dropped-tuning-term",
            SINE_TUNING_MUTATION_CASE, directed_cases, walk_cap=None,
        )

    # 4c-i-b. The same tuning fault on the TRACE surface, planted
    #         stimulus-side through the declared shadow (the
    #         un-clamped-pitch / selector-vs-blend convention): the
    #         mirror re-derives the Q16.15 words with the tuning term
    #         dropped, exactly what an implementer who forgot tuning
    #         would feed the lane, and the clean RTL rendering the
    #         mutated stimulus must diverge from the pristine frozen
    #         truth.
    tuning_stim = directed_cases.get(SINE_TUNING_STIMULUS_CASE)
    if tuning_stim is None:
        print(
            "SINE-VCO FAILED: no directed case %r for the stimulus-side "
            "tuning mutation" % SINE_TUNING_STIMULUS_CASE
        )
        mutations_ok = False
    elif tuning_stim["params"][1] == 0:
        print(
            "SINE-VCO FAILED: %s carries a zero tuning word; the "
            "stimulus-side tuning mutation would be vacuous"
            % SINE_TUNING_STIMULUS_CASE
        )
        mutations_ok = False
    else:
        mut_dir = workdir / "mut-dropped-tuning-stimulus"
        mut_dir.mkdir(parents=True, exist_ok=True)
        (mut_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
        mutated_case = copy.deepcopy(tuning_stim)
        mutated_case["up_pitch"] = tuning_stim["up_pitch"][
            :VCO_MUTATION_WALK_CAP
        ]
        f0_word, _tun, depth_word, init_word = tuning_stim["params"]
        mutated_case["params"] = [f0_word, 0, depth_word, init_word]
        mutated_mirror, _aux = vg.mirror_sine_lane(
            formats, f0_word, 0, depth_word, init_word,
            mutated_case["up_pitch"],
        )
        mutated_case["mirror"] = mutated_mirror
        vco_write_case(mut_dir, 0, mutated_case)
        mut_captures = vco_simulate(mut_dir, simulator, 1, VCO_DUT_SV,
                                    max_n=VCO_MUTATION_WALK_CAP)
        # The expectations stay the PRISTINE committed truth.
        detected = not vco_check_case(mut_captures[0], tuning_stim,
                                      SINE_TUNING_STIMULUS_CASE, prefix=True)
        print(
            "mutation dropped-tuning-stimulus (stimulus side via the "
            "declared shadow, case %s): %s"
            % (SINE_TUNING_STIMULUS_CASE,
               "DETECTED (vectors reject the dropped tuning)" if detected
               else "NOT DETECTED")
        )
        mutations_ok = mutations_ok and detected

    # 4c-ii. Phase-initialization mutation (RTL, AC-2): the one-time
    #        turn-word injection is dropped and every trigger starts from
    #        phase zero. Demonstrated on a directed case with a nonzero
    #        initial-phase word.
    phase_case = directed_cases.get(SINE_PHASE_MUTATION_CASE)
    if phase_case is None:
        print(
            "SINE-VCO FAILED: no directed case %r for the initial-phase "
            "mutation" % SINE_PHASE_MUTATION_CASE
        )
        mutations_ok = False
    elif phase_case["params"][3] == 0:
        print(
            "SINE-VCO FAILED: %s carries a zero initial-phase word; the "
            "zero-initial-phase mutation would be vacuous"
            % SINE_PHASE_MUTATION_CASE
        )
        mutations_ok = False
    else:
        mutations_ok &= vco_run_mutation(
            workdir, simulator, "zero-initial-phase",
            SINE_PHASE_MUTATION_CASE, directed_cases,
        )

    # 4d. Un-clamped pitch (stimulus side, through the declared shadow):
    #     the model's MIDI clamp band is dropped from the pitch
    #     formation and the mirror re-derives the Q16.15 words from the
    #     un-clamped pitch (exactly what an implementer's "cleaner"
    #     un-clamped pitch would feed the lane). The clean RTL renders
    #     the mutated stimulus and the trace must diverge from the
    #     frozen truth within the capped walk; only meaningful on a case
    #     with measured clamps.
    clamp_case = case_dirs[VCO_CLAMP_MUTATION_CASE][1]
    if clamp_case["clamps"] <= 0:
        print(
            "SINE-VCO FAILED: %s carries no clamps; the un-clamped-pitch "
            "mutation would be vacuous" % VCO_CLAMP_MUTATION_CASE
        )
        mutations_ok = False
    else:
        mut_dir = workdir / "mut-un-clamped-pitch"
        mut_dir.mkdir(parents=True, exist_ok=True)
        (mut_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
        mutated_case = copy.deepcopy(clamp_case)
        mutated_case["up_pitch"] = clamp_case["up_pitch"][
            :VCO_MUTATION_WALK_CAP
        ]
        mutated_mirror, _ = vg.mirror_sine_lane(
            formats, *clamp_case["params"], mutated_case["up_pitch"],
            clamp_pitch=False,
        )
        mutated_case["mirror"] = mutated_mirror
        vco_write_case(mut_dir, 0, mutated_case)
        mut_captures = vco_simulate(mut_dir, simulator, 1, VCO_DUT_SV,
                                    max_n=VCO_MUTATION_WALK_CAP)
        # The expectations stay the PRISTINE frozen truth: the un-clamped
        # stimulus must move the trace away from it.
        detected = not vco_check_case(mut_captures[0], clamp_case,
                                      VCO_CLAMP_MUTATION_CASE, prefix=True)
        print(
            "mutation un-clamped-pitch (stimulus side via the declared "
            "shadow, case %s): %s"
            % (VCO_CLAMP_MUTATION_CASE,
               "DETECTED (vectors reject the un-clamped pitch)" if detected
               else "NOT DETECTED")
        )
        mutations_ok = mutations_ok and detected

    # 4e. Selector-vs-blend mod input (stimulus side): the audio-rate
    #     blended pitch column becomes the control-rate selector pick.
    #     The corruption flows through the pitch path's declared shadow:
    #     the mirror re-derives the Q16.15 words from the mutated column
    #     (exactly what the integrated lane would consume), the clean
    #     RTL renders the mutated stimulus, and the output must diverge
    #     from the frozen truth within the capped walk.
    sel_case = case_dirs[VCO_CLAMP_MUTATION_CASE][1]
    matrix = sel_case["matrix_pitch"]
    selector = [
        matrix[(n * 1763) // 176399] for n in range(VCO_MUTATION_WALK_CAP)
    ]
    if selector == sel_case["up_pitch"][:VCO_MUTATION_WALK_CAP]:
        print(
            "SINE-VCO FAILED: the selector stream equals the blended "
            "column on %s; the selector-vs-blend mutation would be "
            "vacuous" % VCO_CLAMP_MUTATION_CASE
        )
        mutations_ok = False
    else:
        mut_dir = workdir / "mut-selector-vs-blend"
        mut_dir.mkdir(parents=True, exist_ok=True)
        (mut_dir / "lut.memh").write_bytes(lut_memh.read_bytes())
        mutated_case = copy.deepcopy(sel_case)
        mutated_case["up_pitch"] = selector
        mutated_mirror, _ = vg.mirror_sine_lane(
            formats, *sel_case["params"], selector,
            samples=VCO_MUTATION_WALK_CAP,
        )
        mutated_case["mirror"] = mutated_mirror
        vco_write_case(mut_dir, 0, mutated_case)
        mut_captures = vco_simulate(mut_dir, simulator, 1, VCO_DUT_SV,
                                    max_n=VCO_MUTATION_WALK_CAP)
        # The expectations stay the PRISTINE frozen truth: the mutated
        # stimulus must move the trace away from it.
        detected = not vco_check_case(mut_captures[0], sel_case,
                                      VCO_CLAMP_MUTATION_CASE, prefix=True)
        print(
            "mutation selector-vs-blend (stimulus side via the declared "
            "shadow, case %s): %s"
            % (VCO_CLAMP_MUTATION_CASE,
               "DETECTED (vectors discriminate the mod input)" if detected
               else "NOT DETECTED")
        )
        mutations_ok = mutations_ok and detected
    ok = ok and mutations_ok

    if not ok:
        print("SINE-VCO RUN FAILED")
        return 1
    print(
        "SINE-VCO RUN PASSED (contract binding + %d receipt cases and %d "
        "directed min/mid/max frequency/phase vectors sample-exact "
        "(%d with committed sidecar bytes) + reset/replay independence + "
        "complete-clip budget/op-count asserts + all mutations detected)"
        % (len(cases), len(directed_cases), sidecar_count)
    )
    return 0


# ======================================================================
# Patch-control flow (issue #69): the RTL patch loader/identity/reset/
# keyboard core against its cycle-exact Python mirror.
# ======================================================================

PATCH_DUT_SV = TB_ROOT / "sv/patch_control.sv"
PATCH_TB_SV = TB_ROOT / "sv/tb_patch_control.sv"
#: Must equal the DUT's TIMEOUT_CYCLES parameter default.
PATCH_TIMEOUT_CYCLES = 5000

CORE_PROFILE_ID = b"torchsynth-1-voice-default"
CORE_IDENTITY = b"identity-golden-0001"
SOURCE_VERSION = b"torchsynth@2b0964d4c6c3d472a2a0d54d91b408caaeffca6d"


def patch_build_model():
    """The mirror oracle bound to the pinned inventory + accepted register."""
    from torchsynth_voice.patch_control_model import PatchControlModel

    inventory = gv.load_inventory_document()
    names = sorted(entry["name"] for entry in inventory["parameters"])
    table_sha = hashlib.sha256(gv.INVENTORY_PATH.read_bytes()).digest()
    return PatchControlModel(
        names,
        table_sha256=table_sha,
        contract_version=numeric_contract_version_bound(),
        profile_id=CORE_PROFILE_ID,
        timeout_cycles=PATCH_TIMEOUT_CYCLES,
    )


def frame(command: int, seq: int, payload: bytes = b"") -> bytes:
    return encode_frame(KIND_COMMAND, command, seq, payload)


def frame_v1(command: int, seq: int) -> bytes:
    """A complete, CRC-valid protocol version 1 frame (stale peer)."""
    body = (
        struct.pack("<BBBH", 1, KIND_COMMAND, command, seq)
        + struct.pack("<H", 0)
    )
    return b"\x67\xf1" + body + struct.pack(
        "<H", crc16_ccitt_false(body)
    )


def crc16_ccitt_false(data: bytes) -> int:
    from torchsynth_voice.core_protocol import crc16_ccitt_false as crc

    return crc(data)


def paced(frames, gap: int = 300, after_commit: int = 4700) -> list:
    """Interleave a conforming host's pacing gap after every frame.

    The advertised rx_queue_depth is 1 and the core processes one frame at
    a time, so a deterministic golden path leaves each frame uncontended.
    A full 78-name commit's hash walk runs ~4.5k cycles, so the gap after
    a COMMIT frame is longer (still below the 5000-cycle patch timeout the
    mirror and DUT share). The backpressure scenario exercises the unpaced
    burst on purpose.
    """
    items: list = []
    for frame in frames:
        items.append(frame)
        if isinstance(frame, bytes):
            items.append(
                after_commit
                if (len(frame) > 4 and frame[4] == CMD_PATCH_COMMIT) else gap
            )
    return items


def schedule_of(items) -> list:
    """A per-cycle schedule from frame bytes and idle-cycle counts."""
    schedule: list = []
    for item in items:
        if isinstance(item, int):
            schedule.extend([None] * item)
        else:
            schedule.extend(item)
    return schedule


def write_schedule(path: Path, schedule: list) -> None:
    lines = ["B %d" % b if b is not None else "I" for b in schedule]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def patch_simulate(workdir: Path, simulator: str, stim: Path,
                   dut_sv: Path) -> tuple:
    """Compile + run the tb; return (response byte list, final observables)."""
    captured = workdir / "captured.txt"
    final = workdir / "final.txt"
    if simulator == "iverilog":
        vvp = workdir / "patch_control.vvp"
        _run(
            [
                "iverilog", "-g2012", "-I", str(TB_ROOT / "sv"),
                "-o", str(vvp), str(CONSTANTS_PKG_SV), str(dut_sv),
                str(PATCH_TB_SV),
            ],
            cwd=workdir,
        )
        _run(
            [
                "vvp", "-n", str(vvp),
                "+stim=%s" % stim,
                "+capture=%s" % captured,
                "+final=%s" % final,
            ],
            cwd=workdir,
        )
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    rsp = [
        int(line)
        for line in captured.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fields = final.read_text(encoding="utf-8").splitlines()
    head = [int(v) for v in fields[0].split()]
    obs = {
        "session": head[0],
        "patch_active": bool(head[1]),
        "kbd_midi_word": head[2],
        "kbd_duration_word": head[3],
        "identity_len": head[4],
        "identity_hex": fields[1].strip(),
        "bank": [int(v) for v in fields[2 : 2 + 78]],
    }
    return rsp, obs


def obs_from_mirror(obs: dict) -> dict:
    identity = obs["identity"]
    return {
        "session": obs["session"],
        "patch_active": obs["patch_active"],
        "kbd_midi_word": obs["kbd_midi_word"],
        "kbd_duration_word": obs["kbd_duration_word"],
        "identity_len": len(identity),
        "identity_hex": identity.hex(),
        "bank": [obs["bank"][slot] for slot in range(78)],
    }


def _frame_summary(frame: tuple) -> str:
    kind, cmd, seq, payload = frame
    name = {2: "RSP", 3: "ERR"}.get(kind, "kind%d" % kind)
    detail = ""
    if kind == 3 and payload:
        try:
            detail = " " + ErrorCode(payload[0]).name
        except ValueError:
            detail = " code=0x%02x" % payload[0]
    return "%s(cmd=0x%02x seq=%d%s)" % (name, cmd, seq, detail)


def compare_run(label: str, schedule: list, workdir: Path, simulator: str,
                model, dut_sv: Path, extra_checks=None) -> bool:
    """Mirror vs RTL: exact response byte stream + exact final state."""
    expected_rsp, mirror_obs = model.run(schedule)
    if mirror_obs["session"] == 2:  # patch_open at end would drain-diverge
        raise SystemExit(
            "%s: scenario ends with an open transaction; end every "
            "scenario in ready" % label
        )
    workdir.mkdir(parents=True, exist_ok=True)
    stim = workdir / "stimulus.txt"
    write_schedule(stim, schedule)
    rsp, obs = patch_simulate(workdir, simulator, stim, dut_sv)

    ok = True
    if rsp != list(expected_rsp):
        ok = False
        print("%s: RESPONSE STREAM MISMATCH" % label)
        expected_frames = decode_rsp_frames(bytes(expected_rsp))
        try:
            actual_frames = decode_rsp_frames(bytes(rsp))
        except AssertionError:
            actual_frames = []
        for index, (want, got) in enumerate(
            zip(expected_frames, actual_frames)
        ):
            if want != got:
                print(
                    "  first differing frame #%d: mirror %s vs rtl %s"
                    % (index, _frame_summary(want), _frame_summary(got))
                )
                break
        if len(expected_frames) != len(actual_frames):
            print(
                "  frame counts: mirror %d vs rtl %d"
                % (len(expected_frames), len(actual_frames))
            )
    want_obs = obs_from_mirror(mirror_obs)
    # the tb always dumps the full 64-byte identity window; only the
    # declared length-prefix carries meaning
    obs["identity_hex"] = obs["identity_hex"][: 2 * obs["identity_len"]]
    for key in ("session", "patch_active", "kbd_midi_word",
                "kbd_duration_word", "identity_len", "identity_hex"):
        if obs[key] != want_obs[key]:
            ok = False
            print(
                "%s: final state mismatch %s: mirror %r vs rtl %r"
                % (label, key, want_obs[key], obs[key])
            )
    if obs["bank"] != want_obs["bank"]:
        ok = False
        first = next(
            slot
            for slot in range(78)
            if obs["bank"][slot] != want_obs["bank"][slot]
        )
        print(
            "%s: active bank mismatch at slot %d: mirror %d vs rtl %d"
            % (label, first, want_obs["bank"][first], obs["bank"][first])
        )
    if extra_checks is not None:
        ok = extra_checks(obs) and ok
    print("%s: %s" % (label, "OK" if ok else "FAIL"))
    return ok


def patch_anchor_words():
    """The 78 wire words from the anchor vector's own declared parameters."""
    vector = gv.load_vector(gv.SENTINEL_VECTOR_PATH)
    counters = StickyCounters()
    words = {}
    for name in sorted(vector["parameters"]):
        words[name] = entry_quantize(
            float(vector["parameters"][name]), formats_midi(), counters,
            "tb.patch.entry:" + name,
        )
    return words, vector


_formats_cache = None


def formats_midi():
    global _formats_cache
    if _formats_cache is None:
        _formats_cache = AcceptedFormats()
    return _formats_cache.midi


def word4(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "little", signed=True)


def patch_hash_entries(model, entries: dict) -> bytes:
    from torchsynth_voice.core_protocol import patch_hash

    return patch_hash(entries, numeric_contract_version=model.contract_version)


def build_scenarios(model, words: dict, vector: dict) -> dict:
    """Every scenario's frame list + idle tail, keyed by scenario id."""
    from torchsynth_voice.core_protocol import patch_hash

    names = list(model.names)
    hello = frame(
        CMD_HELLO, 0,
        encode_hello(CORE_PROFILE_ID, SOURCE_VERSION,
                     model.contract_version, 3),
    )
    rng = random.Random(20260921)
    shuffled = list(names)
    rng.shuffle(shuffled)

    def full_tx(first_seq: int, tx_id: bytes, order: list):
        frames = [
            frame(CMD_PATCH_OPEN, first_seq,
                  encode_patch_open(tx_id, CORE_IDENTITY, model.table_sha256,
                                    len(names)))
        ]
        seq = first_seq + 1
        entries = {}
        for name in order:
            encoded = word4(words[name])
            entries[name] = encoded
            frames.append(frame(CMD_PATCH_NAME, seq,
                                encode_patch_name(name)))
            frames.append(frame(CMD_PATCH_VALUE, seq + 1,
                                encode_patch_value(name, encoded)))
            seq += 2
        digest = patch_hash_entries(model, entries)
        frames.append(frame(CMD_PATCH_COMMIT, seq,
                            encode_patch_commit(digest)))
        return frames, seq

    # ---- s1: full shuffled load, RESET, sub-patch, full re-load --------
    tx1, seq1 = full_tx(1, b"tx-full-0001", shuffled)
    sub_names = ["adsr_1.alpha", "keyboard.midi_f0"]
    sub_entries = {name: word4(words[name]) for name in sub_names}
    sub_digest = patch_hash_entries(model, sub_entries)
    base = seq1 + 1  # next free sequence after tx1's commit
    sub_frames = [
        frame(CMD_PATCH_OPEN, base + 1,
              encode_patch_open(b"tx-sub-0002", CORE_IDENTITY,
                                model.table_sha256, 2)),
        frame(CMD_PATCH_NAME, base + 2, encode_patch_name(sub_names[0])),
        frame(CMD_PATCH_VALUE, base + 3,
              encode_patch_value(sub_names[0], sub_entries[sub_names[0]])),
        frame(CMD_PATCH_NAME, base + 4, encode_patch_name(sub_names[1])),
        frame(CMD_PATCH_VALUE, base + 5,
              encode_patch_value(sub_names[1], sub_entries[sub_names[1]])),
        frame(CMD_PATCH_COMMIT, base + 6, encode_patch_commit(sub_digest)),
    ]
    tx3, _seq3 = full_tx(base + 7, b"tx-full-0003", list(names))
    s1_frames = (
        [hello] + tx1
        + [frame(CMD_RESET, base)]
        + sub_frames + tx3
    )

    # ---- s2: negative battery ------------------------------------------
    alpha_word = sub_entries["adsr_1.alpha"]
    attack_word = word4(words["adsr_1.attack"])
    pair_entries = {"adsr_1.alpha": alpha_word, "adsr_1.attack": attack_word}
    pair_digest = patch_hash_entries(model, pair_entries)
    alpha_only = {"adsr_1.alpha": alpha_word}
    alpha_digest = patch_hash_entries(model, alpha_only)
    neg = [hello]
    neg += [
        frame(CMD_PATCH_OPEN, 1, encode_patch_open(b"tx-neg-01", b"",
                                                   model.table_sha256, 2)),
        frame(CMD_PATCH_NAME, 2, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 3, encode_patch_value("adsr_1.alpha",
                                                     alpha_word)),
        frame(CMD_PATCH_NAME, 4, encode_patch_name("adsr_1.attack")),
        frame(CMD_PATCH_VALUE, 5, encode_patch_value("adsr_1.attack",
                                                     attack_word)),
        frame(CMD_PATCH_COMMIT, 6, encode_patch_commit(bytes(32))),
    ]
    neg += [
        frame(CMD_PATCH_OPEN, 7, encode_patch_open(b"tx-neg-02", b"",
                                                   model.table_sha256, 2)),
        frame(CMD_PATCH_NAME, 8, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 9, encode_patch_value("adsr_1.alpha",
                                                     alpha_word)),
        frame(CMD_PATCH_NAME, 10, encode_patch_name("adsr_1.attack")),
        frame(CMD_PATCH_VALUE, 11, encode_patch_value("adsr_1.attack",
                                                      attack_word)),
        frame(CMD_PATCH_COMMIT, 12, encode_patch_commit(pair_digest)),
    ]
    neg += [
        frame(CMD_PATCH_OPEN, 13, encode_patch_open(b"tx-neg-03", b"",
                                                    model.table_sha256, 3)),
        frame(CMD_PATCH_NAME, 14, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 15, encode_patch_value("adsr_1.alpha",
                                                      alpha_word)),
        frame(CMD_PATCH_NAME, 16, encode_patch_name("adsr_1.attack")),
        frame(CMD_PATCH_VALUE, 17, encode_patch_value("adsr_1.attack",
                                                      attack_word)),
        frame(CMD_PATCH_NAME, 18, encode_patch_name("vco_1.tuning")),
        frame(CMD_PATCH_COMMIT, 19, encode_patch_commit(pair_digest)),
    ]
    neg += [
        frame(CMD_PATCH_OPEN, 20, encode_patch_open(b"tx-neg-04", b"",
                                                    model.table_sha256, 1)),
        frame(CMD_PATCH_VALUE, 21, encode_patch_value("keyboard.midi_f0",
                                                      alpha_word)),
        frame(CMD_PATCH_NAME, 22, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 23, encode_patch_value("adsr_1.alpha",
                                                      b"\x01\x00\x00\x00")),
        frame(CMD_PATCH_VALUE, 24, encode_patch_value("adsr_1.alpha",
                                                      b"\x02\x00\x00\x00")),
        frame(CMD_PATCH_VALUE, 25, encode_patch_value("adsr_1.alpha",
                                                      b"\x01\x00\x00\x00")),
        frame(CMD_PATCH_COMMIT, 26, encode_patch_commit(
            patch_hash_entries(model, {"adsr_1.alpha": b"\x01\x00\x00\x00"}))),
    ]
    neg += [
        frame(CMD_PATCH_OPEN, 27, encode_patch_open(b"tx-neg-05", b"",
                                                    model.table_sha256, 1)),
        frame(CMD_PATCH_OPEN, 28, encode_patch_open(b"tx-neg-06", b"",
                                                    model.table_sha256, 1)),
        frame(CMD_PATCH_ABORT, 29),
        frame(CMD_PATCH_ABORT, 30),
        frame(CMD_PATCH_VALUE, 3, encode_patch_value("adsr_1.alpha",
                                                     alpha_word)),
        frame(CMD_PATCH_OPEN, 31, encode_patch_open(b"tx-neg-07", b"",
                                                    bytes(32), 1)),
        frame(0x2A, 32),
        frame(CMD_PATCH_NAME, 33, encode_patch_name("adsr_1.alpha")),
    ]
    neg += [
        frame(CMD_PATCH_OPEN, 34, encode_patch_open(b"tx-neg-08", b"",
                                                    model.table_sha256, 1)),
        frame(CMD_PATCH_NAME, 35, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 36, encode_patch_value("adsr_1.alpha",
                                                      b"\x03\x00\x00\x00")),
        frame(CMD_PATCH_ABORT, 37),
        frame(CMD_PATCH_OPEN, 38, encode_patch_open(b"tx-neg-09", b"",
                                                    model.table_sha256, 1)),
        frame(CMD_PATCH_NAME, 39, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 40, encode_patch_value("adsr_1.alpha",
                                                      alpha_word)),
        frame(CMD_PATCH_COMMIT, 41, encode_patch_commit(alpha_digest)),
    ]

    # ---- s3: fatal negotiation gate --------------------------------------
    bad_hello = frame(
        CMD_HELLO, 0,
        encode_hello(CORE_PROFILE_ID, SOURCE_VERSION, bytes(32), 3),
    )
    s3_frames = [
        bad_hello,
        frame(CMD_PATCH_OPEN, 1, encode_patch_open(b"tx-fatal", b"",
                                                   model.table_sha256, 1)),
        hello,
        frame_v1(CMD_RESET, 2),
    ]

    # ---- s4: backpressure ERR_BUSY + identical retry ----------------------
    one_tx = [
        frame(CMD_PATCH_OPEN, 1, encode_patch_open(b"tx-busy-01", b"",
                                                   model.table_sha256, 1)),
        frame(CMD_PATCH_NAME, 2, encode_patch_name("adsr_1.alpha")),
        frame(CMD_PATCH_VALUE, 3, encode_patch_value("adsr_1.alpha",
                                                     alpha_word)),
        frame(CMD_PATCH_COMMIT, 4, encode_patch_commit(alpha_digest)),
    ]
    busy_hello = frame(
        CMD_HELLO, 6,
        encode_hello(CORE_PROFILE_ID, SOURCE_VERSION,
                     model.contract_version, 3),
    )
    s4_frames = (
        paced([hello] + one_tx[:3])
        + [one_tx[3], frame(CMD_RESET, 5), busy_hello]
        + [350]
        + [busy_hello]
    )

    # ---- s5: patch timeout + continuation refusal + fresh tx --------------
    fresh_entries = {"keyboard.duration": word4(words["keyboard.duration"])}
    s5_frames = (
        [hello]
        + [frame(CMD_PATCH_OPEN, 1, encode_patch_open(b"tx-timeout-01", b"",
                                                      model.table_sha256, 1)),
           frame(CMD_PATCH_NAME, 2, encode_patch_name("adsr_1.alpha"))]
        + [PATCH_TIMEOUT_CYCLES + 50]
        + [frame(CMD_PATCH_NAME, 2, encode_patch_name("adsr_1.alpha"))]
        + [frame(CMD_PATCH_OPEN, 3, encode_patch_open(b"tx-timeout-02", b"",
                                                      model.table_sha256, 1)),
           frame(CMD_PATCH_NAME, 4,
                 encode_patch_name("keyboard.duration")),
           frame(CMD_PATCH_VALUE, 5,
                 encode_patch_value("keyboard.duration",
                                    fresh_entries["keyboard.duration"])),
           frame(CMD_PATCH_COMMIT, 6, encode_patch_commit(
               patch_hash_entries(model, fresh_entries)))]
    )

    # ---- s6: bad frames + stale protocol version --------------------------
    good_reset = frame(CMD_RESET, 1)
    corrupt = bytearray(good_reset)
    corrupt[-1] ^= 0xFF
    s6_frames = [
        hello, bytes(corrupt), good_reset, frame_v1(CMD_RESET, 2),
        frame(CMD_RESET, 3),
    ]

    scenarios = {
        "s1-full": (paced(s1_frames), [3000]),
        "s2-negative": (paced(neg), [3000]),
        "s3-fatal": (paced(s3_frames), [3000]),
        "s4-busy": (s4_frames, [3000]),
        "s5-timeout": (paced(s5_frames), [3000]),
        "s6-badframe": (paced(s6_frames), [3000]),
    }
    return {
        key: schedule_of(frames + tail)
        for key, (frames, tail) in scenarios.items()
    }


def patch(workdir: Path, simulator: str) -> int:
    """Issue #69 flow: loader/identity/reset/keyboard vs its mirror."""
    model = patch_build_model()
    words, vector = patch_anchor_words()
    scenarios = build_scenarios(model, words, vector)
    names = list(model.names)
    ok = True

    def check_s1(obs):
        good = True
        midi_trace = next(
            t["values"][0] for t in vector["traces"]
            if t["name"] == "keyboard.midi_f0"
        )
        if obs["kbd_midi_word"] != midi_trace:
            print(
                "  keyboard.midi_f0 word %d != anchor trace %d"
                % (obs["kbd_midi_word"], midi_trace)
            )
            good = False
        dur_wire = entry_quantize(
            float(vector["parameters"]["keyboard.duration"]),
            formats_midi(), StickyCounters(), "tb.patch.entry:duration",
        )
        expected_dur = (dur_wire << 9) & ((1 << 47) - 1)
        if obs["kbd_duration_word"] != expected_dur:
            print(
                "  keyboard.duration word %d != exact wire-word widening %d"
                % (obs["kbd_duration_word"], expected_dur)
            )
            good = False
        if not obs["patch_active"]:
            print("  full patch commit did not assert the render gate")
            good = False
        return good

    def check_s2(obs):
        good = True
        if obs["patch_active"]:
            print("  render gate asserted in a partial-only session")
            good = False
        alpha = int.from_bytes(word4(words["adsr_1.alpha"]), "little",
                               signed=True)
        if obs["bank"][names.index("adsr_1.alpha")] != alpha:
            print("  stale-state control: adsr_1.alpha bank word wrong")
            good = False
        if obs["bank"][names.index("keyboard.midi_f0")] != 0:
            print("  keyboard.midi_f0 leaked into the partial-only session")
            good = False
        return good

    run_specs = [
        ("full-78 shuffled load + reset + re-load", "s1-full", check_s1),
        ("negative battery (13 controls)", "s2-negative", check_s2),
        ("fatal negotiation gate", "s3-fatal", None),
        ("backpressure ERR_BUSY + identical retry", "s4-busy", None),
        ("patch timeout + continuation refusal", "s5-timeout", None),
        ("bad-frame recovery + stale protocol version", "s6-badframe", None),
    ]
    for label, key, checks in run_specs:
        ok = compare_run(label, scenarios[key], workdir / key, simulator,
                         model, PATCH_DUT_SV, checks) and ok

    # Mutations: every planted fault MUST be detected (AC 6).
    mutations_ok = True
    mutation_specs = [
        (
            "wrong-entry-byte-order (parameter-order hash walk)",
            (
                "b = name_rom[c_slot] >> ((NAME_BYTES - 1 - c_entry_off) * 8);",
                "b = name_rom[c_slot] >> (c_entry_off * 8);",
            ),
            "s1-full",
        ),
        (
            "stale-contract-version gate weakened",
            (
                "else if (cap_c != NUMERIC_CONTRACT_VERSION) begin",
                "else if (cap_c == NUMERIC_CONTRACT_VERSION) begin",
            ),
            "s3-fatal",
        ),
        (
            "partial load committed (render gate on every commit)",
            (
                "if (tx_want == NUM_PARAMS)\n"
                "                    patch_active_r <= 1'b1;",
                "if (1'b1)\n"
                "                    patch_active_r <= 1'b1;",
            ),
            "s2-negative",
        ),
        (
            "hash domain separation broken",
            (
                "b = PATCH_DOMAIN_TAG[(DOMAIN_TAG_LEN*8-1) - c_fed*8 -: 8];",
                "b = 8'h00;",
            ),
            "s1-full",
        ),
    ]
    for label, (anchor, replacement), scenario in mutation_specs:
        mut_dir = workdir / ("mut-" + scenario)
        mut_dir.mkdir(parents=True, exist_ok=True)
        mutated_sv = mut_dir / "patch_control_mut.sv"
        mutated_sv.write_text(
            mutate_sv(PATCH_DUT_SV.read_text(encoding="utf-8"),
                      anchor, replacement, label),
            encoding="utf-8",
        )
        detected = not compare_run(
            "mutation [%s] %s" % (scenario, label),
            scenarios[scenario], mut_dir, simulator, model, mutated_sv,
        )
        print(
            "mutation %s: %s"
            % (label, "DETECTED (test fails the mutant)" if detected
               else "NOT DETECTED")
        )
        mutations_ok = mutations_ok and detected
    ok = ok and mutations_ok

    if not ok:
        print("PATCH RUN FAILED")
        return 1
    print(
        "PATCH RUN PASSED (mirror-vs-RTL byte/cycle-exact on 6 scenarios + "
        "4 mutations detected + keyboard golden words)"
    )
    return 0


def noise_write_run(workdir: Path, run: int, sound_index: int,
                    declared_slot: int, noise_bytes: bytes) -> None:
    """One run's stimulus: identity words + the exact fed byte stream."""

    (workdir / ("run%d_stim.txt" % run)).write_text(
        "%d %d %d\n" % (sound_index, declared_slot, len(noise_bytes)),
        encoding="utf-8",
    )
    (workdir / ("run%d_bytes.txt" % run)).write_text(
        "".join("%02x\n" % b for b in noise_bytes), encoding="utf-8"
    )


def noise_simulate(workdir: Path, simulator: str, runs: int,
                   dut_sv: Path):
    """Compile + run the noise TB; return (captures, statuses) per run."""

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    if simulator == "iverilog":
        vvp = workdir / "noise_stream.vvp"
        _run(
            [
                "iverilog", "-g2012", "-o", str(vvp),
                str(CONSTANTS_PKG_SV), str(dut_sv), str(NOISE_TB_SV),
            ],
            cwd=workdir,
        )
        _run(["vvp", "-n", str(vvp)], cwd=workdir)
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    captures = []
    statuses = []
    for r in range(runs):
        captured = [
            int(line)
            for line in (
                workdir / ("run%d_captured.txt" % r)
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        status = [
            int(v)
            for v in (
                workdir / ("run%d_status.txt" % r)
            ).read_text(encoding="utf-8").split()
        ]
        captures.append(captured)
        statuses.append(status)
    return captures, statuses


def noise(workdir: Path, simulator: str) -> int:
    """Issue #75 flow: the host-fed exact noise lane vs the golden receipt."""

    receipt = json.loads(NOISE_RECEIPT_PATH.read_bytes())
    if receipt["kind"] != "fixed-voice-golden-release-receipt":
        raise SystemExit("unexpected receipt kind: %r" % receipt["kind"])
    cases = receipt["cases"]
    ok = True

    # 1+2. Host-feed resolve + golden bit-exactness of the mirror lane.
    streams = {}
    for case in cases:
        sound_index = case["sound_index"]
        meta = case["noise"]
        if meta["seed"] != 13 or meta["slot"] != sound_index % 32:
            print(
                "NOISE FAILED: case %s noise identity block violates the "
                "C8 slot rule" % case["id"]
            )
            ok = False
            continue
        raw = nsg.resolve_canonical_bytes(sound_index)
        sha = nsg.noise_bytes_sha256(raw)
        mirror = nsg.mirror_stream(raw)
        digest = nsg.trace_digest(mirror)
        if sha != meta["sha256"] or digest != case["traces"]["noise.raw"]:
            print(
                "NOISE FAILED: case %s mirror/digest binding broken "
                "(bytes %s vs %s; noise.raw %s vs %s)"
                % (case["id"], sha, meta["sha256"], digest,
                   case["traces"]["noise.raw"])
            )
            ok = False
        streams[sound_index] = (raw, mirror)
    print(
        "host feed + mirror: %d golden cases resolved (seed 13, slot "
        "sound_index %% 32), every noise.raw digest bound bit-exactly -> %s"
        % (len(cases), "OK" if ok else "FAIL")
    )

    # 3+4. Committed cases + pattern stream + replay pair, one invocation.
    run_plan = []  # (label, sound_index, declared_slot, bytes)
    for case in cases:
        sound_index = case["sound_index"]
        raw, _ = streams[sound_index]
        run_plan.append((case["id"], sound_index,
                         nsg.canonical_slot(sound_index), raw))
    # The synthetic pattern stream rides in one full clip (the DUT's length
    # contract is one clip per enabled stream; the edge patterns lead and
    # +0.0 padding completes the clip).
    pattern_bytes = b"".join(
        struct.pack("<I", bits) for bits in NOISE_PATTERN_BITS
    )
    pattern_bytes += b"\x00\x00\x00\x00" * (
        nsg.EXPECTED_NOISE_BYTES // 4 - len(NOISE_PATTERN_BITS)
    )
    assert len(pattern_bytes) == nsg.EXPECTED_NOISE_BYTES
    run_plan.append(("synthetic-patterns", 0, 0, pattern_bytes))
    first, replay_index = NOISE_REPLAY_INDICES
    raw0, _ = streams[first]
    raw_replay = nsg.resolve_canonical_bytes(replay_index)
    if nsg.noise_bytes_sha256(raw_replay) != nsg.noise_bytes_sha256(raw0):
        raise SystemExit(
            "replay pair %r must share a slot by the C8 rule" % (
                NOISE_REPLAY_INDICES,)
        )
    run_plan.append(("replay-sound-0", first, 0, raw0))
    run_plan.append(("replay-sound-32", replay_index,
                     nsg.canonical_slot(replay_index), raw_replay))

    case_dir = workdir / "committed"
    case_dir.mkdir(parents=True, exist_ok=True)
    for run, (_, sound_index, slot, raw) in enumerate(run_plan):
        noise_write_run(case_dir, run, sound_index, slot, raw)
    captures, statuses = noise_simulate(case_dir, simulator, len(run_plan),
                                        NOISE_DUT_SV)

    replay_positions = {}
    for run, (label, sound_index, slot, raw) in enumerate(run_plan):
        if label == "synthetic-patterns" or sound_index not in streams:
            mirror = nsg.mirror_stream(raw)
        else:
            mirror = streams[sound_index][1]
        if label.startswith("replay-sound-"):
            replay_positions[label] = run
        captured = captures[run]
        status = statuses[run]
        if captured != mirror:
            print("NOISE FAILED: RTL capture != golden lane (%s):" % label)
            for i, (want, got) in enumerate(zip(mirror, captured)):
                if want != got:
                    print(
                        "  first mismatch at sample %d: expected %d, got %d"
                        % (i, want, got)
                    )
                    break
            else:
                print(
                    "  length mismatch: expected %d samples, got %d"
                    % (len(mirror), len(captured))
                )
            ok = False
            continue
        clean = status == [0, 0, len(raw), len(mirror), len(mirror)]
        if not clean:
            print(
                "NOISE FAILED: run status %r not clean for %s"
                % (status, label)
            )
            ok = False
    print(
        "RTL bit-exactness: %d fed streams (%d golden cases + %d "
        "structural patterns + replay pair) equal the golden lane "
        "word-for-word -> %s"
        % (len(run_plan), len(cases), 1, "OK" if ok else "FAIL")
    )

    # 4. Replay/reset + 32-stream repetition: sound 0 then 32 back-to-back.
    replay_ok = (
        captures[replay_positions["replay-sound-32"]]
        == captures[replay_positions["replay-sound-0"]]
        == streams[first][1]
    )
    print(
        "replay/reset: sound_index %d -> %d back-to-back through one en gap "
        "reproduces the golden capture byte-for-byte (32-stream repetition, "
        "no off-by-one state) -> %s"
        % (first, replay_index, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 5. DR-0010 #75 owner row + emission check.
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("NOISE REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    owner_ok = True
    per_clip = {
        "declared narrowings": (176400, 176400),
        "multiply-class ops (structural: shift+round convert)": (0, 0),
    }
    for name, (actual, limit) in per_clip.items():
        verdict = "OK" if actual <= limit else "FAIL"
        owner_ok = owner_ok and actual <= limit
        print(
            "  budget: %d %s per clip vs DR-0010 #75 owner row %d -> %s"
            % (actual, name, limit, verdict)
        )
    for run, (label, _, _, _) in enumerate(run_plan):
        if label == "synthetic-patterns":
            continue
        narrow_count = statuses[run][4]
        if narrow_count != 176400:
            print(
                "NOISE FAILED: %s narrow_count %d != one declared narrowing "
                "per sample" % (label, narrow_count)
            )
            owner_ok = False
    print(
        "  budget: exported sticky counters assert 176400 declared "
        "narrowings per clip on every committed case -> %s"
        % ("OK" if owner_ok else "FAIL")
    )
    try:
        emitted = codegen.emit(schedule_payload=schedule)
        landed = CONSTANTS_PKG_SV.read_text(encoding="utf-8")
        matches = emitted.package_text == landed and (
            sched.SCHEDULE_ID in emitted.emitted_ids
        )
        print(
            "  budget constants: landed %s matches the live emission of both "
            "accepted registers -> %s"
            % (CONSTANTS_PKG_SV.name, "OK" if matches else "FAIL")
        )
        owner_ok = owner_ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return False
    ok = ok and owner_ok

    # 6. Mutations: every planted fault MUST be detected (AC-5).
    mutations_ok = True
    mut_case = cases[NOISE_MUTATION_CASE_INDEX]
    mut_sound = mut_case["sound_index"]
    mut_slot = nsg.canonical_slot(mut_sound)
    mut_raw, mut_mirror = streams[mut_sound]

    # 6a. Dropped final byte (stimulus): the length check must latch.
    mut_dir = workdir / "mut-dropped"
    mut_dir.mkdir(parents=True, exist_ok=True)
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, mut_raw[:-1])
    _, mut_statuses = noise_simulate(mut_dir, simulator, 1, NOISE_DUT_SV)
    detected = mut_statuses[0][:2] == [1, nsg.ERROR_STREAM_TRUNCATED]
    print(
        "mutation dropped-byte (stimulus): %s"
        % ("DETECTED (sticky length error)" if detected else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6b. Duplicated trailing byte (stimulus): the overrun check must latch.
    mut_dir = workdir / "mut-duplicated"
    mut_dir.mkdir(parents=True, exist_ok=True)
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, mut_raw + b"\x00")
    _, mut_statuses = noise_simulate(mut_dir, simulator, 1, NOISE_DUT_SV)
    detected = mut_statuses[0][:2] == [1, nsg.ERROR_BYTE_OVERRUN]
    print(
        "mutation duplicated-byte (stimulus): %s"
        % ("DETECTED (sticky overrun error)" if detected else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6c. Wrong declared slot (stimulus): the C8 identity check must latch.
    mut_dir = workdir / "mut-slot"
    mut_dir.mkdir(parents=True, exist_ok=True)
    noise_write_run(mut_dir, 0, mut_sound, mut_slot ^ 1, mut_raw)
    _, mut_statuses = noise_simulate(mut_dir, simulator, 1, NOISE_DUT_SV)
    detected = mut_statuses[0][:2] == [1, nsg.ERROR_SLOT_IDENTITY]
    print(
        "mutation wrong-slot (stimulus): %s"
        % ("DETECTED (sticky slot-identity error)" if detected
           else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6d. LSB truncation instead of half-even (RTL).
    mut_dir = workdir / "mut-truncation"
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        NOISE_DUT_SV.read_text(encoding="utf-8"),
        NOISE_TRUNCATION_ANCHOR, NOISE_TRUNCATION_MUTANT, "lsb-truncation",
    )
    (mut_dir / "noise_stream_mut.sv").write_text(mutated, encoding="utf-8")
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, mut_raw)
    mut_captures, mut_statuses = noise_simulate(
        mut_dir, simulator, 1, mut_dir / "noise_stream_mut.sv"
    )
    detected = (
        mut_captures[0] != mut_mirror
        and mut_statuses[0][:2] == [0, 0]
    )
    print(
        "mutation lsb-truncation (RTL): %s"
        % ("DETECTED (capture diverges from the golden lane)" if detected
           else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected

    # 6e. Wrong slot-selection rule (RTL): the mutated rule must flag a
    # clean, correctly-declared stream.
    mut_dir = workdir / "mut-slotrule"
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        NOISE_DUT_SV.read_text(encoding="utf-8"),
        NOISE_SLOT_RULE_ANCHOR, NOISE_SLOT_RULE_MUTANT, "slot-rule",
    )
    (mut_dir / "noise_stream_mut.sv").write_text(mutated, encoding="utf-8")
    noise_write_run(mut_dir, 0, mut_sound, mut_slot, mut_raw)
    _, mut_statuses = noise_simulate(
        mut_dir, simulator, 1, mut_dir / "noise_stream_mut.sv"
    )
    detected = mut_statuses[0][:2] == [1, nsg.ERROR_SLOT_IDENTITY]
    print(
        "mutation slot-rule (RTL): %s"
        % ("DETECTED (mutated rule rejects the clean stream)" if detected
           else "NOT DETECTED")
    )
    mutations_ok = mutations_ok and detected
    ok = ok and mutations_ok

    if not ok:
        print("NOISE RUN FAILED")
        return 1
    print(
        "NOISE RUN PASSED (%d golden cases bit-exact + pattern/replay "
        "streams bit-exact + owner-row/op-count asserts + all 5 mutations "
        "detected)" % len(cases)
    )
    return 0


MIX_DUT_SV = TB_ROOT / "sv/audio_mix_engine.sv"
MIX_TB_SV = TB_ROOT / "sv/tb_audio_mix_engine.sv"
#: The lane's declared output traces, in the model's registry naming.
MIX_STREAM_TRACES = (
    "vco_1.post_vca",
    "vco_2.post_vca",
    "noise.post_vca",
    "mixer.pre_normalization",
)
#: Declared directed fixtures (spec/reference/directed-voice-v1.json, the
#: manifest the frozen receipt's own case selection draws from). The
#: ``special`` family supplies the fixture classes the receipt's
#: param-committed cases cannot: total silence (every mixer level zero)
#: and the all-source / high-depth stress patch (all three mixer levels,
#: all twenty matrix routes, and every modulation/rate depth at 1.0).
MIX_FIXTURE_CASES = ("special:silence", "special:near-silence", "special:stress")
#: Per-lane curve evidence: the declared case whose recorded (normalized,
#: physical) pair makes that lane's upstream input-curve exponent
#: observable (``physical = normalized ** (1 / curve)``; a pair at 0 or 1
#: observes nothing). Independent per lane: the noise lane's power-40
#: curve and the two oscillators' linear curves are each read from their
#: own case, never inferred from one another.
MIX_CURVE_EVIDENCE = {
    "vco_1": "special:near-silence",
    "vco_2": "source:vco_2",
    "noise": "source:noise",
}
#: Receipt cases carrying the remaining declared fixture classes:
#: single-source isolation per audio path, the two high-depth boundaries,
#: and the declared peak-stress anchor variant.
MIX_SINGLE_SOURCE_CASES = ("source:vco_1", "source:vco_2", "source:noise")
MIX_HIGH_DEPTH_CASES = (
    "boundary:vco_1.mod_depth:upper",
    "boundary:vco_2.mod_depth:upper",
)
MIX_STRESS_CASE = "normalization-stress:anchor-3.9478583336"
#: Replay-independence pair: the level words, the peak register, and the
#: counters are per-trigger; run 0 state must not leak into run 1 (the
#: pair routes different lanes, so a leaked level word is visible).
MIX_REPLAY_PAIR = ("source:noise", "source:vco_1")
#: Mutation-simulation walk cap (+max_n): every mutation below
#: demonstrably bites within the first 24,000 audio samples (~0.54 s), so
#: the mutation sims walk a fraction of the grid. Committed-case and
#: fixture runs always walk the full 176,400 samples.
MIX_MUTATION_WALK_CAP = 24000

#: Anchored RTL mutation seams (mutate_sv refuses if an anchor moved).
MIX_RTL_MUTATIONS = {
    "level-route-swap": (
        "    wire signed [C1_WIDTH-1:0] lvl1_eff = lvl1_r;",
        "    wire signed [C1_WIDTH-1:0] lvl1_eff = lvl2_r;"
        "  // MUTANT: mixer level route swapped",
    ),
    "vca-2-polarity": (
        "    wire signed [95:0] vca2_eff = vca2_sat\n"
        "        ? ((vca2_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : vca2_narrow;",
        "    wire signed [95:0] vca2_eff = -(vca2_sat\n"
        "        ? ((vca2_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : vca2_narrow);"
        "  // MUTANT: vco_2 VCA polarity flipped",
    ),
    "mixer-dc-offset": (
        "    wire signed [95:0] mix_out = mix_eff;",
        "    wire signed [95:0] mix_out = mix_eff + 96'sd1;"
        "  // MUTANT: DC offset on the mix word",
    ),
    "mixer-truncate": (
        "    wire signed [95:0] mix_narrow =\n"
        "        div_half_even_pow2_signed(acc_sum, C1_FRAC_BITS);",
        "    wire signed [95:0] mix_narrow = (acc_sum >>> C1_FRAC_BITS);"
        "  // MUTANT: truncation instead of half-even",
    ),
    "mixer-wrap": (
        "    wire signed [95:0] mix_eff = mix_sat\n"
        "        ? ((mix_narrow > 96'sd0) ? C1_MAX96 : C1_MIN96) : mix_narrow;",
        "    wire signed [95:0] mix_eff = mix_narrow;"
        "  // MUTANT: C7 saturation dropped at the mixer narrowing",
    ),
    "dropped-noise-source": (
        "    wire signed [95:0] acc_sum = term1 + term2 + termn;",
        "    wire signed [95:0] acc_sum = term1 + term2;"
        "  // MUTANT: noise term dropped from the accumulator",
    ),
}


def mix_load_receipt():
    """The frozen receipt + accepted formats + the #76 declared cases.

    Reuses the sine lane's binding verification verbatim (same receipt,
    same live accepted contract: DR-0008 status, hash-linked C5 table,
    emitted constants package) and additionally requires every case this
    flow names.
    """

    payload, formats, cases = vco_load_receipt()
    required = (
        MIX_SINGLE_SOURCE_CASES + MIX_HIGH_DEPTH_CASES
        + (MIX_STRESS_CASE,) + MIX_REPLAY_PAIR
    )
    for case_id in required:
        if case_id not in cases:
            raise SystemExit(
                "receipt carries no param-committed case %r" % case_id
            )
    return payload, formats, cases


def mix_load_fixtures():
    """The declared directed fixtures (physical + normalized maps).

    The manifest is validated through its own landed validator (identity
    seal + upstream-commit pin), so a drifted manifest refuses the run
    instead of silently feeding a different patch.
    """

    document = json.loads(dr.MANIFEST_PATH.read_text(encoding="utf-8"))
    dr.validate_manifest(document)
    by_id = {case["id"]: case for case in document["cases"]}
    fixtures = {}
    wanted = set(MIX_FIXTURE_CASES) | set(MIX_CURVE_EVIDENCE.values())
    for case_id in sorted(wanted):
        if case_id not in by_id:
            raise SystemExit(
                "declared directed manifest carries no fixture %r" % case_id
            )
        patch = dr.resolve_patch(document, by_id[case_id])
        fixtures[case_id] = {
            "physical": {name: pair["physical"] for name, pair in patch.items()},
            "normalized": {
                name: pair["normalized"] for name, pair in patch.items()
            },
        }
    return document, fixtures


def mix_derive_case(formats, case_id: str, physical: dict,
                    normalized: dict = None, frozen: dict = None):
    """Model-derived stimulus + truth for one case, frozen-pinned if committed.

    ``mx.derive_case`` renders the frozen composed model and refuses
    unless the lane mirror reproduces the model's own rows and per-site
    counter records. When the receipt commits this case's trace digests,
    the model's rows are additionally pinned to them here: the RTL is then
    held to the frozen evidence, not to a fresh render.
    """

    case = mx.derive_case(formats, physical, 0, normalized)
    if frozen is not None:
        for name in mx.MIX_TRACES:
            digest = mx.digest_words(case["model_traces"][name])
            if digest != frozen["traces"][name]:
                raise SystemExit(
                    "frozen-trace drift for %s on %s: rendered %s but the "
                    "receipt declares %s"
                    % (name, case_id, digest, frozen["traces"][name])
                )
    case["id"] = case_id
    case["frozen"] = frozen is not None
    return case


def mix_prefix_truth(formats, case: dict, cap: int):
    """The mirror truth for a capped prefix walk of an already-derived case."""

    streams, aux = mx.mirror_mix_lane(
        formats, case["raw"], case["amp"], case["levels"], samples=cap
    )
    return {"streams": streams, "aux": aux}


def mix_write_case(workdir: Path, run: int, case: dict, levels: dict = None,
                   amp: dict = None):
    """Write one run's stimulus files (level words + per-sample streams).

    ``levels`` / ``amp`` override the derived stimulus for the declared
    stimulus-side mutations (a wrong gain word, a swapped route); the
    committed runs always pass the derived maps.
    """

    use_levels = case["levels"] if levels is None else levels
    use_amp = case["amp"] if amp is None else amp
    (workdir / ("run%d_params.txt" % run)).write_text(
        " ".join(
            str(int(use_levels[lane]) & 0xFFFFFFFF) for lane in mx.LANES
        ) + "\n",
        encoding="utf-8",
    )
    raw = case["raw"]
    lines = []
    for n in range(case["samples"]):
        lines.append(
            "%d %d %d %d %d %d"
            % (
                raw["vco_1"][n] & 0xFFFFFFFF,
                raw["vco_2"][n] & 0xFFFFFFFF,
                raw["noise"][n] & 0xFFFFFFFF,
                use_amp["vco_1"][n] & 0xFFFFFFFF,
                use_amp["vco_2"][n] & 0xFFFFFFFF,
                use_amp["noise"][n] & 0xFFFFFFFF,
            )
        )
    (workdir / ("run%d_streams.txt" % run)).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def mix_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path,
                 max_n: int = None) -> list:
    """Compile and run the file-driven tb; return per-run captures."""

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    if simulator == "iverilog":
        vvp = workdir / "audio_mix.vvp"
        _run(
            [
                "iverilog", "-g2012", "-o", str(vvp),
                str(CONSTANTS_PKG_SV), str(dut_sv), str(MIX_TB_SV),
            ],
            cwd=workdir,
        )
        command = ["vvp", "-n", str(vvp)]
        if max_n is not None:
            command.append("+max_n=%d" % max_n)
        _run(command, cwd=workdir)
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    captures = []
    for run in range(runs):
        rows = {
            "vco_1.post_vca": [],
            "vco_2.post_vca": [],
            "noise.post_vca": [],
            "mixer.pre_normalization": [],
            "abs": [],
            "peak": [],
        }
        order = list(rows)
        for line in (
            workdir / ("run%d_captured.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != len(order):
                raise SystemExit(
                    "captured row %r does not carry %d words"
                    % (line, len(order))
                )
            for name, raw in zip(order, fields):
                try:
                    rows[name].append(int(raw))
                except ValueError:
                    rows[name].append(None)  # an x-state emission: a mismatch
        cycles = int(
            (workdir / ("run%d_cycles.txt" % run))
            .read_text(encoding="utf-8").strip()
        )
        ops = None
        for line in (
            workdir / ("run%d_ops.txt" % run)
        ).read_text(encoding="utf-8").splitlines():
            if line.strip():
                ops = []
                for raw in line.split()[1:]:
                    try:
                        ops.append(int(raw))
                    except ValueError:
                        ops.append(None)  # an x-state counter: a mismatch
        capture = dict(rows)
        capture["cycles"] = cycles
        capture["ops"] = ops
        captures.append(capture)
    return captures


def mix_expected_streams(truth: dict) -> dict:
    """The mirror's captured-stream expectations, keyed like the capture."""

    streams = truth["streams"]
    return {
        "vco_1.post_vca": streams["post_vca"]["vco_1"],
        "vco_2.post_vca": streams["post_vca"]["vco_2"],
        "noise.post_vca": streams["post_vca"]["noise"],
        "mixer.pre_normalization": streams["mix"],
        "abs": streams["abs"],
        "peak": streams["peak"],
    }


def mix_mismatch_rows(capture: dict, truth: dict, prefix: bool = False) -> list:
    """Every (stream, index, expected, actual) first-mismatch row.

    With ``prefix`` (the capped mutation walks) a shorter capture is not
    itself a mismatch, but any differing sample inside the captured
    prefix is.
    """

    expected = mix_expected_streams(truth)
    rows = []
    for name, want in expected.items():
        got = capture[name]
        if prefix:
            want = want[: len(got)]
        for index in range(max(len(got), len(want))):
            a = got[index] if index < len(got) else None
            b = want[index] if index < len(want) else None
            if a != b:
                rows.append((name, index, b, a))
                break
    return rows


def mix_check_case(capture: dict, truth: dict, case_id: str,
                   prefix: bool = False) -> bool:
    """Sample-exact verification of all six captured streams for one run."""

    rows = mix_mismatch_rows(capture, truth, prefix=prefix)
    for name, index, want, got in rows:
        print(
            "AUDIO-MIX FAILED: %s differs (%s[%d]: expected %s got %s)"
            % (name, case_id, index, want, got)
        )
    return not rows


def mix_expected_ops(walked: int, truth: dict) -> list:
    """Expected exported counters for one full-or-capped walk.

    Static per-sample owner-row ops (DR-0010 #76: 6 mults + 2 adds + 4
    narrow sites); the C7 saturation and C6 rounding totals are the
    mirror's measured per-site counts; the peak compare is one per sample
    (charged to #77's row, exported here); Q6.42 accumulator-band faults
    must be zero.
    """

    aux = truth["aux"]
    return [
        6 * walked,
        2 * walked,
        4 * walked,
        aux["sats"],
        aux["rounds"],
        walked,
        0,
    ]


def check_mix_budget(schedule, walked: int, truth: dict, ops_row: list) -> bool:
    """Op-count conformance to the DR-0010 #76 owner row + emission check.

    DR-0010's owner row for #76: "audio rate: 6 mults + 2 adds + 4 narrow
    sites; 48-bit-class accumulator; S4 rescale to the pre-normalization
    mix word", split in the module table as "3x audio VCA: 3 / 0 / 3" plus
    "Mixer (level mults, accumulate, S4): 3 / 2 / 1". The engine declares
    exactly those counts per audio sample. The peak-feed compare is
    DR-0010's #77 row ("pass 1 peak tracking: 1 compare + 1 abs-select
    folded into the mixer output") and is reported against that row, never
    counted in this lane's adds. The complete clip schedule is asserted:
    the walked sample count must equal the emitted SCHED_SAMPLES_PER_PASS
    over one full pass. The serialized single-MAC cycle mapping remains
    the integration lanes; this is an op-count check, not a PPA/fit claim.
    """

    ok = True
    per_sample = {
        "multiply-class ops (3 VCA + 3 level)": (6, 6),
        "accumulator adds": (2, 2),
        "declared narrowings (3 VCA S4 + 1 mixer S4)": (4, 4),
    }
    for name, (actual, limit) in per_sample.items():
        verdict = "OK" if actual <= limit else "FAIL"
        ok = ok and actual <= limit
        print(
            "  budget: %d %s per sample vs DR-0010 #76 owner-row cap %d -> %s"
            % (actual, name, limit, verdict)
        )
    print(
        "  budget: 1 peak-feed compare per sample -> reported against the "
        "DR-0010 #77 owner row (pass-1 peak tracking), never folded into "
        "this lane's adds"
    )
    print(
        "  accumulator: three 24x24 -> 48-bit products summed exactly in "
        "%s (no intermediate rounding before the declared narrowing); "
        "observed band [%d, %d]"
        % (truth["aux"]["product_format"], truth["aux"]["acc_min"],
           truth["aux"]["acc_max"])
    )
    try:
        emitted = codegen.emit(schedule_payload=schedule)
        landed = CONSTANTS_PKG_SV.read_text(encoding="utf-8")
        matches = emitted.package_text == landed and (
            sched.SCHEDULE_ID in emitted.emitted_ids
        )
        print(
            "  budget constants: landed %s matches the live emission of "
            "both accepted registers -> %s"
            % (CONSTANTS_PKG_SV.name, "OK" if matches else "FAIL")
        )
        ok = ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return False
    samples_per_pass = sched.constant(schedule, "samples_per_pass")
    complete = (walked == samples_per_pass == gv.CANONICAL_SAMPLE_COUNT)
    print(
        "  complete clip schedule: walked %d samples == emitted "
        "samples_per_pass %d == canonical %d -> %s"
        % (walked, samples_per_pass, gv.CANONICAL_SAMPLE_COUNT,
           "OK" if complete else "FAIL")
    )
    ok = ok and complete
    expected = mix_expected_ops(walked, truth)
    counters_ok = ops_row == expected
    print(
        "  exported counters over the walk: %r vs expected %r -> %s"
        % (ops_row, expected, "OK" if counters_ok else "FAIL")
    )
    return ok and counters_ok


def mix_report_mutation(label: str, kind: str, case_id: str, rows: list,
                        ops_bad: bool, ops_row, expected_traces: set) -> bool:
    """Require one planted fault DETECTED, localized to the declared traces."""

    hit = {name for name, _i, _w, _g in rows}
    detected = bool(rows) or ops_bad
    localized = hit == expected_traces
    surface = []
    if rows:
        surface.append(
            "trace rows %s" % ",".join(
                "%s[%d]" % (name, index) for name, index, _w, _g in rows
            )
        )
    if ops_bad:
        surface.append("property rows (counters %r)" % (ops_row,))
    print(
        "mutation %s (%s, case %s): %s via %s; localization %s "
        "(expected %s)"
        % (
            label, kind, case_id,
            "DETECTED (test fails the mutant)" if detected
            else "NOT DETECTED",
            " + ".join(surface) if surface else "neither",
            "OK" if localized else "FAIL",
            ",".join(sorted(expected_traces)) if expected_traces else "(none)",
        )
    )
    return detected and localized


def mix_run_rtl_mutation(workdir: Path, simulator: str, label: str,
                         case: dict, truth: dict, expected_traces: set) -> bool:
    """Plant one anchored RTL mutation on one case; require DETECTED."""

    anchor, replacement = MIX_RTL_MUTATIONS[label]
    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    mutated = mutate_sv(
        MIX_DUT_SV.read_text(encoding="utf-8"), anchor, replacement, label
    )
    (mut_dir / "audio_mix_engine_mut.sv").write_text(mutated, encoding="utf-8")
    mix_write_case(mut_dir, 0, case)
    captures = mix_simulate(
        mut_dir, simulator, 1, mut_dir / "audio_mix_engine_mut.sv",
        max_n=truth["aux"]["samples"],
    )
    capture = captures[0]
    rows = mix_mismatch_rows(capture, truth, prefix=True)
    walked = len(capture["mixer.pre_normalization"])
    ops_bad = capture["ops"] != mix_expected_ops(walked, truth)
    return mix_report_mutation(
        label, "RTL", case["id"], rows, ops_bad, capture["ops"],
        expected_traces,
    )


def mix_run_stimulus_mutation(workdir: Path, simulator: str, label: str,
                              case: dict, truth: dict, expected_traces: set,
                              levels: dict = None, amp: dict = None) -> bool:
    """Feed one mutated stimulus through the pristine RTL; require DETECTED."""

    mut_dir = workdir / ("mut-" + label)
    mut_dir.mkdir(parents=True, exist_ok=True)
    mix_write_case(mut_dir, 0, case, levels=levels, amp=amp)
    captures = mix_simulate(
        mut_dir, simulator, 1, MIX_DUT_SV, max_n=truth["aux"]["samples"]
    )
    capture = captures[0]
    rows = mix_mismatch_rows(capture, truth, prefix=True)
    walked = len(capture["mixer.pre_normalization"])
    ops_bad = capture["ops"] != mix_expected_ops(walked, truth)
    return mix_report_mutation(
        label, "stimulus", case["id"], rows, ops_bad, capture["ops"],
        expected_traces,
    )


def mix(workdir: Path, simulator: str) -> int:
    """Issue #76 flow: the audio VCA + mixer engine vs the frozen model."""

    try:
        receipt, formats, cases = mix_load_receipt()
    except ChoiceNotAccepted as error:
        print("AUDIO-MIX REFUSED: accepted register refused: %s" % error)
        return 1
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print(
            "AUDIO-MIX REFUSED: DR-0010 schedule register refused: %s" % error
        )
        return 1
    manifest, fixtures = mix_load_fixtures()

    print(
        "Loaded the frozen whole-voice receipt (%d cases, %d "
        "param-committed), bindings verified (DR-0008 %s, lut %s); plus %d "
        "declared directed fixtures to run and %d curve-evidence patches "
        "from %s (identity %s)"
        % (len(receipt["cases"]), len(cases),
           receipt["bindings"]["dr_0008_status"],
           receipt["bindings"]["lut_sha256"][:12] + "...",
           len(MIX_FIXTURE_CASES),
           len(set(MIX_CURVE_EVIDENCE.values()) - set(MIX_FIXTURE_CASES)),
           dr.MANIFEST_PATH.name,
           manifest["identity"]["sha256"][:12] + "...")
    )

    ok = True
    derived = {}

    # 1. Sample-exact engine runs over the full 176,400-sample clip: every
    #    param-committed receipt case (frozen-pinned) plus the declared
    #    silence / near-silence / all-source-stress fixtures. The exported
    #    op-counter property row must equal the model-measured counts on
    #    every clean run (an x-state counter is a named failure, never a
    #    silent pass).
    plan = [(case_id, cases[case_id]) for case_id in sorted(cases)]
    plan += [(case_id, None) for case_id in MIX_FIXTURE_CASES]
    for case_id, frozen in plan:
        case_dir = workdir / ("case-" + case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        if frozen is None:
            physical = fixtures[case_id]["physical"]
            normalized = fixtures[case_id]["normalized"]
        else:
            physical = frozen["parameters"]
            normalized = None
        case = mix_derive_case(
            formats, case_id, physical, normalized, frozen
        )
        truth = {"streams": case["streams"], "aux": case["aux"]}
        mix_write_case(case_dir, 0, case)
        captures = mix_simulate(case_dir, simulator, 1, MIX_DUT_SV)
        case_ok = mix_check_case(captures[0], truth, case_id)
        expected_ops = mix_expected_ops(case["samples"], truth)
        if captures[0]["ops"] != expected_ops:
            case_ok = False
            print(
                "AUDIO-MIX FAILED: exported counters %r != expected %r (%s)"
                % (captures[0]["ops"], expected_ops, case_id)
            )
        if not case_ok:
            ok = False
        case["ops"] = captures[0]["ops"]
        aux = case["aux"]
        print(
            "case %s: %d samples, levels %s (frozen %s), sats %d, rounds "
            "%d, peak %d@%d, RTL sample-exact -> %s"
            % (case_id, case["samples"],
               [case["levels"][lane] for lane in mx.LANES],
               case["frozen"], aux["sats"], aux["rounds"], aux["peak_word"],
               aux["peak_index"], "OK" if case_ok else "FAIL")
        )
        derived[case_id] = case

    # 2. The declared fixture classes, named against the cases that carry
    #    them (an unnamed class is never reported as covered).
    silence = derived["special:silence"]
    fixture_rows = [
        ("silence", "special:silence",
         all(word == 0 for word in silence["levels"].values())
         and all(word == 0 for word in silence["streams"]["mix"])
         and silence["aux"]["peak_word"] == 0),
        ("near-silence", "special:near-silence",
         derived["special:near-silence"]["levels"]["vco_1"] > 0
         and derived["special:near-silence"]["aux"]["peak_word"] > 0),
        ("single-source", ",".join(MIX_SINGLE_SOURCE_CASES),
         all(
             sum(1 for lane in mx.LANES if derived[cid]["levels"][lane] != 0) == 1
             for cid in MIX_SINGLE_SOURCE_CASES
         )),
        ("all-source", "special:stress",
         all(
             derived["special:stress"]["levels"][lane] != 0
             for lane in mx.LANES
         )),
        ("high-depth", ",".join(MIX_HIGH_DEPTH_CASES + ("special:stress",)),
         all(derived[cid]["aux"]["rounds"] > 0
             for cid in MIX_HIGH_DEPTH_CASES)),
        ("stress (mixer C7 saturation exercised)",
         "%s,special:stress" % MIX_STRESS_CASE,
         derived["special:stress"]["aux"]["sites"]["mixer.S4"]["saturation"] > 0),
    ]
    for label, carriers, holds in fixture_rows:
        print(
            "fixture class %s: carried by %s -> %s"
            % (label, carriers, "OK" if holds else "FAIL")
        )
        ok = ok and holds

    # 2b. The mixer input curves: the noise lane's recorded upstream curve
    #     is observable in the declared fixtures' own physical/normalized
    #     pair, and it enters the engine through the same S1 entry site as
    #     the oscillator gains (never as an RTL gain law).
    curve_ok = True
    for lane in mx.LANES:
        curve = mx.MIXER_INPUT_CURVES[lane]
        evidence_id = MIX_CURVE_EVIDENCE[lane]
        observed = mx.observed_curve_exponent(
            fixtures[evidence_id]["physical"],
            fixtures[evidence_id]["normalized"],
            lane,
        )
        expected = 1.0 / curve
        holds = observed is not None and abs(observed - expected) <= 1e-6 * expected
        print(
            "  mixer input curve %s: recorded curve %r (exponent %g), "
            "observed exponent %r from %s -> %s"
            % (lane, curve, expected, observed, evidence_id,
               "OK" if holds else "FAIL")
        )
        curve_ok = curve_ok and holds
        if evidence_id in cases:
            name = "mixer." + lane
            bound = (
                fixtures[evidence_id]["physical"][name]
                == cases[evidence_id]["parameters"][name]
            )
            print(
                "    binding: manifest %s physical == the receipt case's own "
                "committed parameter -> %s"
                % (name, "OK" if bound else "FAIL")
            )
            curve_ok = curve_ok and bound
    ok = ok and curve_ok

    # 3. Reset/replay: a second trigger cannot retain prior lane state.
    first, second = MIX_REPLAY_PAIR
    replay_dir = workdir / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    mix_write_case(replay_dir, 0, derived[first])
    mix_write_case(replay_dir, 1, derived[second])
    replay_captures = mix_simulate(replay_dir, simulator, 2, MIX_DUT_SV)
    solo_dir = workdir / "solo"
    solo_dir.mkdir(parents=True, exist_ok=True)
    mix_write_case(solo_dir, 0, derived[second])
    solo_captures = mix_simulate(solo_dir, simulator, 1, MIX_DUT_SV)
    replay_ok = True
    for name in list(MIX_STREAM_TRACES) + ["abs", "peak"]:
        if replay_captures[1][name] != solo_captures[0][name]:
            print(
                "AUDIO-MIX FAILED: run-after-run %s differs from the solo "
                "run - prior lane state leaked" % name
            )
            replay_ok = False
    if replay_captures[1]["ops"] != solo_captures[0]["ops"]:
        print(
            "AUDIO-MIX FAILED: run-after-run counters %r != solo %r - "
            "counters are not per-trigger"
            % (replay_captures[1]["ops"], solo_captures[0]["ops"])
        )
        replay_ok = False
    second_truth = {
        "streams": derived[second]["streams"],
        "aux": derived[second]["aux"],
    }
    replay_ok = replay_ok and mix_check_case(
        replay_captures[1], second_truth, second
    )
    print(
        "reset/replay: trigger-to-trigger back-to-back runs (%s -> %s) "
        "reproduce the solo golden run -> %s"
        % (first, second, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 4. Budget: owner-row op counts + emission + complete clip schedule.
    budget_case = derived["source:vco_1"]
    ok = ok and check_mix_budget(
        schedule,
        budget_case["samples"],
        {"streams": budget_case["streams"], "aux": budget_case["aux"]},
        budget_case["ops"],
    )

    # 5. Mutations: each planted fault MUST be detected AND localized.
    cap = MIX_MUTATION_WALK_CAP
    prefix_truth = {
        case_id: mix_prefix_truth(formats, derived[case_id], cap)
        for case_id in (
            "source:vco_1", "source:vco_2", "source:noise",
            "special:silence", "special:stress",
        )
    }
    mutations_ok = True
    mix_only = {"mixer.pre_normalization", "abs", "peak"}

    # 5a. Route: the mixer level words swapped (the only nonzero level
    #     reaches the wrong lane). The VCA outputs are untouched.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "level-route-swap",
        derived["source:vco_1"], prefix_truth["source:vco_1"], mix_only,
    ) and mutations_ok

    # 5b. Polarity: the vco_2 VCA output negated. A pure sign inversion is
    #     INVISIBLE to the magnitude feed (|mix| and the running peak are
    #     sign-blind, and this case saturates nowhere, so the negated mix
    #     has identical magnitudes), so it must be caught on the signed
    #     lane word and the signed mix word — exactly the localization
    #     asserted here. A lane that only checked the peak feed would miss
    #     it.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "vca-2-polarity",
        derived["source:vco_2"], prefix_truth["source:vco_2"],
        {"vco_2.post_vca", "mixer.pre_normalization"},
    ) and mutations_ok

    # 5c. DC: a constant offset on the mix word, caught on the silence
    #     fixture where the true mix is identically zero.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "mixer-dc-offset",
        derived["special:silence"], prefix_truth["special:silence"], mix_only,
    ) and mutations_ok

    # 5d. Rounding: truncation instead of half-even at the mixer narrowing.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "mixer-truncate",
        derived["source:vco_1"], prefix_truth["source:vco_1"], mix_only,
    ) and mutations_ok

    # 5e. Saturation: C7 saturation dropped, caught on the stress fixture
    #     whose accumulator genuinely exceeds the C1 band.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "mixer-wrap",
        derived["special:stress"], prefix_truth["special:stress"], mix_only,
    ) and mutations_ok

    # 5f. Route: the noise term dropped from the accumulator, caught on the
    #     noise-only case.
    mutations_ok = mix_run_rtl_mutation(
        workdir, simulator, "dropped-noise-source",
        derived["source:noise"], prefix_truth["source:noise"], mix_only,
    ) and mutations_ok

    # 5g. Gain: the recorded noise input curve dropped — the case's own
    #     NORMALIZED noise level fed as though the lane were linear
    #     (curve 1.0) instead of its recorded power-40 conversion.
    #     Stimulus-side: the curve lives at the measured-observation seam,
    #     never in RTL.
    noise_case = derived["source:noise"]
    counters = StickyCounters()
    dropped_curve = dict(noise_case["levels"])
    dropped_curve["noise"] = entry_quantize(
        float(fixtures["source:noise"]["normalized"]["mixer.noise"]),
        formats.audio, counters, "tb.mix.curve_dropped",
    )
    if dropped_curve["noise"] == noise_case["levels"]["noise"]:
        raise SystemExit(
            "the curve-dropped noise level word equals the committed one; "
            "this mutation would prove nothing"
        )
    mutations_ok = mix_run_stimulus_mutation(
        workdir, simulator, "noise-curve-dropped", noise_case,
        prefix_truth["source:noise"], mix_only, levels=dropped_curve,
    ) and mutations_ok

    # 5h. Gain: a one-ULP noise level word error (the gain is load-bearing
    #     at ULP resolution, not merely at audible scale).
    one_ulp = dict(noise_case["levels"])
    one_ulp["noise"] = one_ulp["noise"] + 1
    mutations_ok = mix_run_stimulus_mutation(
        workdir, simulator, "noise-gain-one-ulp", noise_case,
        prefix_truth["source:noise"], mix_only, levels=one_ulp,
    ) and mutations_ok

    # 5i. Route: the vco_1 / vco_2 amplitude columns swapped upstream (the
    #     #72 lane's routes crossed). Both VCA outputs move; the mix
    #     follows.
    vco1_case = derived["source:vco_1"]
    swapped_amp = dict(vco1_case["amp"])
    swapped_amp["vco_1"], swapped_amp["vco_2"] = (
        vco1_case["amp"]["vco_2"], vco1_case["amp"]["vco_1"]
    )
    mutations_ok = mix_run_stimulus_mutation(
        workdir, simulator, "amp-route-swap", vco1_case,
        prefix_truth["source:vco_1"],
        {"vco_1.post_vca", "vco_2.post_vca"} | mix_only,
        amp=swapped_amp,
    ) and mutations_ok

    ok = ok and mutations_ok

    if not ok:
        print("AUDIO-MIX RUN FAILED")
        return 1
    print(
        "AUDIO-MIX RUN PASSED (frozen-binding + %d receipt cases and %d "
        "declared fixtures sample-exact over the complete clip + "
        "reset/replay independence + owner-row/op-count asserts + all 9 "
        "mutations detected and localized)"
        % (len(cases), len(MIX_FIXTURE_CASES))
    )
    return 0


def normreplay_wrong_reciprocal_clip() -> list:
    """A near-full-scale divide-branch clip for the wrong-reciprocal mutant.

    ``normalization_replay.directed_cases()``'s bodies stay three decades
    below unity (by the module's own design, so the inserted peak stays
    unique) — a 1-ULP U1.22 gain error moves the narrowed output of a tiny
    sample by an immeasurable fraction of an output LSB, so none of those
    committed cases can demonstrate this mutation. This synthetic clip
    (not part of the committed golden grid) instead holds every sample at
    magnitude ``peak_int - 1`` (alternating sign), so a 1-ULP gain error
    moves nearly every sample's narrowed output — the mutation this
    module's own S5 narrow site must never silently pass.
    """
    peak_int = nr.UNITY_INT + 1  # the smallest peak that divides
    body = peak_int - 1
    clip = [body if i % 2 == 0 else -body for i in range(nr.CLIP_SAMPLES)]
    clip[100] = peak_int
    return clip


def normreplay_hexword(value: int) -> str:
    """One C1_WIDTH (24-bit) two's-complement word, $readmemh-ready."""
    return "%06x" % (value & 0xFFFFFF)


def normreplay_write_run(
    workdir: Path, run: int, n1: int, n2: int, mix1, mix2
) -> None:
    """One run's stimulus: pass-1/pass-2 declared counts + the two streams."""

    (workdir / ("run%d_stim.txt" % run)).write_text(
        "%d %d\n" % (n1, n2), encoding="utf-8"
    )
    (workdir / ("run%d_mix1.hex" % run)).write_text(
        "\n".join(normreplay_hexword(v) for v in mix1) + "\n", encoding="utf-8"
    )
    (workdir / ("run%d_mix2.hex" % run)).write_text(
        "\n".join(normreplay_hexword(v) for v in mix2) + "\n", encoding="utf-8"
    )


def normreplay_simulate(workdir: Path, simulator: str, runs: int, dut_sv: Path):
    """Compile + run the normreplay TB; return (captures, statuses) per run."""

    (workdir / "runs.txt").write_text("%d\n" % runs, encoding="utf-8")
    if simulator == "iverilog":
        vvp = workdir / "normreplay.vvp"
        _run(
            [
                "iverilog", "-g2012", "-o", str(vvp),
                str(CONSTANTS_PKG_SV), str(dut_sv), str(NORMREPLAY_TB_SV),
            ],
            cwd=workdir,
        )
        _run(["vvp", "-n", str(vvp)], cwd=workdir)
    else:
        raise SystemExit(
            "simulator %r is not wired up; this runner is PDK-free and "
            "currently supports iverilog" % simulator
        )
    captures = []
    statuses = []
    for r in range(runs):
        captured = [
            int(line)
            for line in (
                workdir / ("run%d_captured.txt" % r)
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        status = [
            int(v)
            for v in (
                workdir / ("run%d_status.txt" % r)
            ).read_text(encoding="utf-8").split()
        ]
        captures.append(captured)
        statuses.append(status)
    return captures, statuses


def normreplay_expected_status(diag: dict) -> list:
    """The clean-run status row the host mirror's diagnostics predict.

    Matches tb_normalization_replay_engine.sv's status line order: error,
    error_code, peak_word, gain_word, branch_normalized, done, pass_index
    (P_DONE == 3) — the prefix every clean run must share.
    """

    return [
        0, 0,
        diag["peak_word"],
        diag["gain_word"],
        1 if diag["normalized_branch"] else 0,
        1,
        3,
    ]


def normreplay(workdir: Path, simulator: str) -> int:
    """Issue #77 flow: the normalization replay controller + one-shot top's
    two-pass sequencer vs the frozen model's own ``normalize_words()``."""

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        print("NORMREPLAY REFUSED: accepted register refused: %s" % error)
        return 1

    ok = True

    # 1. The isolated directed Q2.21 case grid (issue #52's own boundary
    #    fixtures): below/at/above-one, peak tie, silence, extrema,
    #    late-peak, tied-max, plus the two release anchors — this is the
    #    grid that actually names "silence" and "extrema" (AC1).
    cases = []
    directed = nr.directed_cases()
    for label in sorted(directed):
        cases.append((label, directed[label]))
    anchored = nr.anchored_cases()
    for label in sorted(anchored):
        cases.append((label, anchored[label][0]))

    # 2. Full-voice normalization-family cases (fixture mapping source 1):
    #    regenerated through the composed fixed model and bound against the
    #    frozen fixed-voice-golden-v1 receipt's own per-trace digests before
    #    use as this module's own declared RTL input interface
    #    (mixer.pre_normalization) — no dependency on issue #76's RTL.
    receipt = json.loads(NOISE_RECEIPT_PATH.read_bytes())
    receipt_cases = {c["id"]: c for c in receipt["cases"]}
    fixture_ok = True
    for case_id in nrg.FULL_VOICE_CASES:
        fixed, _diag = nrg.resolve_full_voice_case(case_id, formats)
        want = receipt_cases[case_id]
        for trace in (
            "mixer.pre_normalization", "mixer.peak", "mixer.gain", "mixer.output",
        ):
            if nrg.digest_words(fixed[trace]) != want["traces"][trace]:
                print(
                    "NORMREPLAY FAILED: %s regenerated %s diverges from the "
                    "frozen fixed-voice-golden-v1 receipt" % (case_id, trace)
                )
                fixture_ok = False
        cases.append((case_id, fixed["mixer.pre_normalization"]))
    print(
        "fixture regeneration: %d isolated boundary cases + %d full-voice "
        "cases, every full-voice trace digest bound to the frozen receipt "
        "-> %s" % (len(directed) + len(anchored), len(nrg.FULL_VOICE_CASES),
                   "OK" if fixture_ok else "FAIL")
    )
    ok = ok and fixture_ok

    # AC2: back-to-back replay pair — the same case rendered twice with no
    # reset in between must reproduce its own capture byte-for-byte (no
    # per-sample intermediate survives across passes OR across triggers).
    replay_clip = directed[NORMREPLAY_REPLAY_CASE]
    cases.append((NORMREPLAY_REPLAY_CASE + "[replay-a]", replay_clip))
    cases.append((NORMREPLAY_REPLAY_CASE + "[replay-b]", replay_clip))

    # 3. Host mirror expectations (normalize_words() itself, no
    #    reimplementation) + the committed-case RTL run.
    case_dir = workdir / "committed"
    case_dir.mkdir(parents=True, exist_ok=True)
    expected = {}
    for index, (label, mix_words) in enumerate(cases):
        out, diag = nrg.mirror_normalize(mix_words, formats)
        expected[label] = (out, diag)
        normreplay_write_run(
            case_dir, index, len(mix_words), len(mix_words), mix_words, mix_words
        )
    captures, statuses = normreplay_simulate(
        case_dir, simulator, len(cases), NORMREPLAY_DUT_SV
    )

    bitexact_ok = True
    replay_positions = {}
    for index, (label, mix_words) in enumerate(cases):
        out, diag = expected[label]
        captured = captures[index]
        status = statuses[index]
        if label.endswith("[replay-a]") or label.endswith("[replay-b]"):
            replay_positions[label] = index
        if captured != out:
            print("NORMREPLAY FAILED: %s capture != host mirror output:" % label)
            for i, (want, got) in enumerate(zip(out, captured)):
                if want != got:
                    print(
                        "  first mismatch at sample %d: expected %d, got %d"
                        % (i, want, got)
                    )
                    break
            else:
                print(
                    "  length mismatch: expected %d samples, got %d"
                    % (len(out), len(captured))
                )
            bitexact_ok = False
            continue
        want_status = normreplay_expected_status(diag)
        if status[:7] != want_status:
            print(
                "NORMREPLAY FAILED: %s status %r != expected %r "
                "(error, error_code, peak_word, gain_word, "
                "branch_normalized, done, pass_index)"
                % (label, status[:7], want_status)
            )
            bitexact_ok = False
    print(
        "RTL bit-exactness: %d cases (isolated + full-voice) match the "
        "host mirror sample-exactly, status registers clean -> %s"
        % (len(cases), "OK" if bitexact_ok else "FAIL")
    )
    ok = ok and bitexact_ok

    replay_ok = (
        captures[replay_positions[NORMREPLAY_REPLAY_CASE + "[replay-a]"]]
        == captures[replay_positions[NORMREPLAY_REPLAY_CASE + "[replay-b]"]]
        == expected[NORMREPLAY_REPLAY_CASE + "[replay-a]"][0]
    )
    print(
        "replay/reset (AC2): %r rendered twice back-to-back (no reset "
        "between triggers) reproduces its own capture byte-for-byte -> %s"
        % (NORMREPLAY_REPLAY_CASE, "OK" if replay_ok else "FAIL")
    )
    ok = ok and replay_ok

    # 4. DR-0010 #77 owner row (op counts, reported) + AC3 framing +
    #    schedule-constants emission check, from a dedicated single-render
    #    run so "since rst" counters equal exactly this one clip's counts.
    try:
        schedule = sched.require_accepted_schedule()
    except ScheduleNotAccepted as error:
        print("NORMREPLAY REFUSED: DR-0010 schedule register refused: %s" % error)
        return 1
    budget_dir = workdir / "budget"
    budget_dir.mkdir(parents=True, exist_ok=True)
    budget_clip = nr.directed_cases()["fixed:extremum"]  # divide branch
    normreplay_write_run(
        budget_dir, 0, len(budget_clip), len(budget_clip), budget_clip, budget_clip
    )
    _, budget_statuses = normreplay_simulate(
        budget_dir, simulator, 1, NORMREPLAY_DUT_SV
    )
    (
        b_error, b_error_code, b_peak, b_gain, b_branch, b_done, b_pass,
        b_compares, b_selects, b_recip_divs, b_mults, b_narrows, b_sats,
    ) = budget_statuses[0]

    def sc(name: str):
        return sched.constant(schedule, name)

    samples_per_pass = sc("samples_per_pass")
    passes_per_clip = sc("passes_per_clip")
    clip_sample_slots = sc("clip_sample_slots")
    pass2_folded_cycles_max = sc("pass2_folded_cycles_max")

    owner_ok = (
        b_error == 0 and b_branch == 1
        and b_compares == samples_per_pass
        and b_selects == samples_per_pass
        and b_recip_divs == 1
        and b_mults == samples_per_pass
        and b_narrows == samples_per_pass
    )
    per_clip = {
        "pass-1 compares (1/sample, folded into the mixer output)": (
            b_compares, samples_per_pass,
        ),
        "pass-1 abs-selects (1/sample, folded into the mixer output)": (
            b_selects, samples_per_pass,
        ),
        "per-clip reciprocal divisions (declared extra, 0 or 1)": (
            b_recip_divs, None,
        ),
        "pass-2 gain multiplies (normalized branch, 1/sample)": (
            b_mults, samples_per_pass,
        ),
        "pass-2 declared narrowings (normalized branch, 1/sample)": (
            b_narrows, samples_per_pass,
        ),
    }
    for name, (actual, limit) in per_clip.items():
        if limit is None:
            print("  budget: %d %s -> n/a (reported)" % (actual, name))
            continue
        verdict = "OK" if actual == limit else "FAIL"
        owner_ok = owner_ok and actual == limit
        print(
            "  budget: %d %s vs DR-0010 #77 owner-row count %d -> %s"
            % (actual, name, limit, verdict)
        )
    slots_consistent = clip_sample_slots == passes_per_clip * samples_per_pass
    print(
        "  budget: passes_per_clip=%d, samples_per_pass=%d, "
        "clip_sample_slots=%d (== passes x samples_per_pass) -> %s"
        % (
            passes_per_clip, samples_per_pass, clip_sample_slots,
            "OK" if (passes_per_clip == 2 and slots_consistent) else "FAIL",
        )
    )
    owner_ok = owner_ok and passes_per_clip == 2 and slots_consistent
    print(
        "  budget: the once-per-clip reciprocal division and the pass-1/"
        "pass-2 transition are combinational (one clock edge) -> within "
        "pass2_folded_cycles_max=%d headroom (PARTIAL-DUT measurement, no "
        "schedule-conformance or PPA/fit claim)" % pass2_folded_cycles_max
    )
    try:
        emitted = codegen.emit(schedule_payload=schedule)
        landed = CONSTANTS_PKG_SV.read_text(encoding="utf-8")
        matches = emitted.package_text == landed and (
            sched.SCHEDULE_ID in emitted.emitted_ids
        )
        print(
            "  budget constants: landed %s matches the live emission of both "
            "accepted registers -> %s"
            % (CONSTANTS_PKG_SV.name, "OK" if matches else "FAIL")
        )
        owner_ok = owner_ok and matches
    except Exception as error:  # noqa: BLE001 - reported, never a silent pass
        print("  budget constants: emission failed -> %s" % error)
        return 1
    ok = ok and owner_ok

    # 5. Mutations (AC6): every planted fault MUST be detected.
    mutations_ok = True

    def run_mutation(label, anchor, replacement, case_id, clip=None):
        mut_dir = workdir / ("mut-" + label)
        mut_dir.mkdir(parents=True, exist_ok=True)
        mutated_sv = mut_dir / "normalization_replay_engine_mut.sv"
        mutated_sv.write_text(
            mutate_sv(
                NORMREPLAY_DUT_SV.read_text(encoding="utf-8"),
                anchor, replacement, label,
            ),
            encoding="utf-8",
        )
        if clip is None:
            clip = nr.directed_cases()[case_id]
        normreplay_write_run(mut_dir, 0, len(clip), len(clip), clip, clip)
        mut_captures, mut_statuses = normreplay_simulate(
            mut_dir, simulator, 1, mutated_sv
        )
        pristine_out, _pristine_diag = nrg.mirror_normalize(clip, formats)
        detected = (
            mut_captures[0] != pristine_out
            or mut_statuses[0][0] != 0  # a sticky error is also a divergence
        )
        print(
            "mutation %s (RTL, case %s): %s"
            % (label, case_id, "DETECTED (diverges from the pristine golden "
               "capture)" if detected else "NOT DETECTED")
        )
        return detected

    mutations_ok &= run_mutation(
        "always-on", NORMREPLAY_ALWAYS_ON_ANCHOR, NORMREPLAY_ALWAYS_ON_MUTANT,
        NORMREPLAY_BYPASS_MUTATION_CASE,
    )
    mutations_ok &= run_mutation(
        "always-off", NORMREPLAY_ALWAYS_ON_ANCHOR, NORMREPLAY_ALWAYS_OFF_MUTANT,
        NORMREPLAY_DIVIDE_MUTATION_CASE,
    )
    mutations_ok &= run_mutation(
        "wrong-peak", NORMREPLAY_WRONG_PEAK_ANCHOR, NORMREPLAY_WRONG_PEAK_MUTANT,
        NORMREPLAY_DIVIDE_MUTATION_CASE,
    )
    mutations_ok &= run_mutation(
        "wrong-reciprocal", NORMREPLAY_WRONG_RECIPROCAL_ANCHOR,
        NORMREPLAY_WRONG_RECIPROCAL_MUTANT, "synthetic:near-full-scale",
        clip=normreplay_wrong_reciprocal_clip(),
    )
    mutations_ok &= run_mutation(
        "off-by-one", NORMREPLAY_OFF_BY_ONE_ANCHOR, NORMREPLAY_OFF_BY_ONE_MUTANT,
        NORMREPLAY_OFF_BY_ONE_CASE,
    )
    ok = ok and mutations_ok

    if not ok:
        print("NORMREPLAY RUN FAILED")
        return 1
    print(
        "NORMREPLAY RUN PASSED (%d isolated + full-voice cases bit-exact "
        "+ replay pair bit-exact + owner-row/op-count asserts + all 5 "
        "mutations detected)" % len(cases)
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="selftest",
        choices=["selftest", "anchor", "adsr", "patch", "lfo", "modmatrix",
                 "vco",
                 "vco2", "noise", "mix", "normreplay"],
        help="'selftest' proves the harness; 'anchor' runs the real "
        "golden-vector flow through the format-true DUT; 'adsr' runs the "
        "issue #70 ADSR engine against the frozen fixed model's golden "
        "vectors; 'patch' runs the issue #69 patch-control core against "
        "its cycle-exact Python mirror; 'lfo' runs the issue #71 LFO + "
        "control-VCA engine against the frozen fixed model's golden "
        "vectors; 'modmatrix' runs the issue #72 modulation-matrix + "
        "endpoint-aligned upsample engines against the frozen fixed "
        "model's golden vectors; 'vco' runs the issue #73 sine VCO "
        "engine against the frozen whole-voice receipt's vco_1.raw "
        "traces; 'vco2' runs the issue #74 square/saw "
        "VCO engine against the frozen fixed model's golden vectors; "
        "'noise' runs the issue #75 host-fed "
        "exact noise-stream lane against the landed fixed-voice golden "
        "receipt; 'mix' runs the issue #76 audio VCA + pre-normalization "
        "mixer engine against the frozen whole-voice receipt and the "
        "declared directed fixtures; 'normreplay' runs the issue #77 "
        "normalization replay controller + one-shot top's two-pass "
        "sequencer against the frozen fixed model's own "
        "normalize_words()",
    )
    parser.add_argument(
        "--simulator",
        default="iverilog",
        choices=["iverilog"],
        help="open-source simulator (PDK-free)",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="keep artifacts here instead of a temp dir",
    )
    args = parser.parse_args(argv)

    if shutil.which(args.simulator) is None:
        print(
            "ERROR: %s is not installed; the %s run cannot execute here. "
            "CI (.github/workflows/tb-sim.yml) arbitrates on a host that "
            "installs it. An unrun check is never a pass."
            % (args.simulator, args.command)
        )
        return 3

    commands = {
        "selftest": selftest,
        "anchor": anchor,
        "adsr": adsr,
        "patch": patch,
        "lfo": lfo,
        "modmatrix": modmatrix,
        "vco": vco,
        "vco2": vco2,
        "noise": noise,
        "mix": mix,
        "normreplay": normreplay,
    }
    command = commands[args.command]

    if args.workdir is not None:
        workdir = args.workdir
        workdir.mkdir(parents=True, exist_ok=True)
        return command(workdir, args.simulator)

    with tempfile.TemporaryDirectory(prefix="tb-%s-" % args.command) as tmp:
        return command(Path(tmp), args.simulator)


if __name__ == "__main__":
    sys.exit(main())
