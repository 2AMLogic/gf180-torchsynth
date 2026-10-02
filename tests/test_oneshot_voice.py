"""Integrated whole-voice one-shot top, harness-level checks (issue #79).

These checks run everywhere and need no simulator. They prove:

- the comparison machinery (first-mismatch localization by trace / cycle /
  sample, exact status and op-counter checks, x-state emissions treated as
  mismatches, and -- explicitly -- that a one-ULP difference anywhere fails,
  i.e. there is no tolerance);
- that the compared trace set is exactly the frozen model's declared
  checkpoint set (nothing silently uncompared);
- that every mutation seam's anchor occurs exactly once in the RTL it names,
  so a drifted source refuses instead of silently testing nothing, and that
  every planted mutant still elaborates;
- that the declared case partition agrees with the frozen receipt, that the
  ``directed`` profile is a strict superset of ``regression``, and that the
  ends of the normalization peak envelope (a zero peak and the accepted audio
  format's ceiling) are reachable **only** through the directed vectors --
  the claim that profile exists to discharge;
- that the closed-form upsample interior count agrees with the model's own
  coordinate table;
- that the CI lane's step budgets fit under their job cap.

The full simulation flow (``tb/run_voice.py``) takes tens of minutes and is
arbitrated by CI / the remote box; it is NOT run by this suite, and this
suite passing is never evidence that it passed. The elaboration tests below
are skipped, not passed, where Icarus is absent.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tb"))

import run_voice as rv  # noqa: E402

RECEIPT = ROOT / "sim/reference/fixed-voice-golden-v1.json"
CONTROL_TICKS = 1764


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None


def synthetic_expected(n=8, full=False):
    """A small but structurally exact expectation for the comparator."""

    ctl = {
        name: [(index + 1) * (tick + 1) for tick in range(CONTROL_TICKS)]
        for index, name in enumerate(rv.VOICE_CTL_TRACES)
    }
    audio = {
        name: [(-1) ** j * ((index + 2) * (j + 1)) for j in range(n)]
        for index, name in enumerate(rv.VOICE_AUDIO_TRACES)
    }
    audio["vco_1.midi_sum"] = [7 * (j + 3) for j in range(n)]
    out = [w * 2 for w in audio["mixer.pre_normalization"]] if full else None
    status = [0, 0, 8, 4194304, 1, 1, 3, n, n, 1, n, n, 0, 8] if full else None
    ops = {}
    for index in range(6):
        ops[("A", index)] = [4 * CONTROL_TICKS, 2 * CONTROL_TICKS,
                             3 * CONTROL_TICKS, 3 * CONTROL_TICKS]
    for index in range(2):
        ops[("L", index)] = [6 * CONTROL_TICKS, 10 * CONTROL_TICKS,
                             CONTROL_TICKS, 0]
    ops[("M",)] = [20 * CONTROL_TICKS, 15 * CONTROL_TICKS,
                   5 * CONTROL_TICKS, 0]
    for pas in (1, 2):
        for route in range(5):
            # Position 5 is the upsample engine's sticky saturation counter
            # (#254). It is a real expectation at every walk length, never
            # None -- a None here would make the comparator skip it.
            ops[("U", pas, route)] = [n - 1, n - 1, n - 1, n - 1, n, 0, n]
        ops[("V1", pas)] = [3 * n, 7 * n, 4 * n, n, None, None]
        ops[("V2", pas)] = [8 * n, 8 * n, 7 * n, None]
        ops[("MIX", pas)] = [6 * n, 2 * n, 4 * n, None, None, n, 0]
        ops[("NZ", pas)] = [4 * n, n, n, 0, 0, 0]
    return {"ctl": ctl, "audio": audio, "out": out, "status": status,
            "ops": ops, "walk": n, "full": full}


def clean_capture(expected):
    """The capture a conforming RTL would produce for ``expected``."""

    n = expected["walk"]
    ctl_rows = [
        [tick] + [expected["ctl"][name][tick] for name in rv.VOICE_CTL_TRACES]
        for tick in range(CONTROL_TICKS)
    ]

    def audio_rows(pas, base):
        return [
            [base + j, pas, pas]
            + [expected["audio"][name][j] for name in rv.VOICE_AUDIO_TRACES]
            + [expected["audio"][name][j] for name in rv.VOICE_AUX_AUDIO_TRACES]
            for j in range(n)
        ]

    def link_rows(pas, base):
        return [
            [base + j, pas, expected["audio"]["mixer.pre_normalization"][j]]
            for j in range(n)
        ]

    capture = {
        "ctl": ctl_rows,
        "audio1": audio_rows(1, 10_000),
        "audio2": audio_rows(2, 20_000),
        "link1": link_rows(1, 10_000),
        "link2": link_rows(2, 20_000),
        "out": ([(30_000 + i, w) for i, w in enumerate(expected["out"])]
                if expected["out"] is not None else []),
        "status": list(expected["status"]) if expected["status"] else [],
        "ops": {
            key: [0 if v is None else v for v in row]
            for key, row in expected["ops"].items()
        },
    }
    return capture


class ComparatorLocalizesFirstMismatch(unittest.TestCase):
    def test_clean_capture_has_no_rows(self):
        expected = synthetic_expected(full=True)
        self.assertEqual(rv.voice_rows(clean_capture(expected), expected), [])

    def test_status_names_match_status_width(self):
        self.assertEqual(
            len(rv.VOICE_STATUS_NAMES),
            len(synthetic_expected(full=True)["status"]),
        )

    def test_wrong_control_tick_names_trace_and_tick(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        col = rv.VOICE_CTL_TRACES.index("lfo_2.raw") + 1
        capture["ctl"][7][col] += 1
        rows = rv.voice_rows(capture, expected)
        self.assertEqual(rows[0][0], "lfo_2.raw")
        self.assertEqual(rows[0][2], 7)

    def test_wrong_audio_sample_names_trace_cycle_sample(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        col = rv.VOICE_AUDIO_TRACES.index("vco_2.raw") + 3
        capture["audio1"][3][col] += 1
        rows = rv.voice_rows(capture, expected)
        self.assertEqual(rows[0][0], "vco_2.raw[pass1]")
        self.assertEqual(rows[0][1], 10_003)
        self.assertEqual(rows[0][2], 3)

    def test_second_pass_is_compared_independently(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        col = rv.VOICE_AUDIO_TRACES.index("mixer.pre_normalization") + 3
        capture["audio2"][5][col] += 1
        rows = rv.voice_rows(capture, expected)
        self.assertEqual(rows[0][0], "mixer.pre_normalization[pass2]")
        self.assertEqual(rows[0][2], 5)

    def test_link_drop_is_localized_to_the_dropped_sample(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        del capture["link1"][4]
        rows = rv.voice_rows(capture, expected)
        self.assertEqual(rows[0][0], "link.replay_input[pass1]")
        self.assertEqual(rows[0][2], 4)

    def test_sequence_rows_are_ordered_by_cycle(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        # A late control-tick fault and an early audio fault: the audio row
        # carries a cycle number and must sort ahead of the untimed one.
        capture["ctl"][1000][1] += 1
        capture["audio1"][0][3] += 1
        rows = rv.voice_rows(capture, expected)
        self.assertEqual(rows[0][1], 10_000)

    def test_no_tolerance_one_ulp_output_difference_fails(self):
        expected = synthetic_expected(n=16, full=True)
        capture = clean_capture(expected)
        cycle, word = capture["out"][11]
        capture["out"][11] = (cycle, word + 1)
        rows = rv.voice_rows(capture, expected)
        self.assertEqual(rows[0][0], "mixer.output")
        self.assertEqual(rows[0][2], 11)

    def test_no_tolerance_one_ulp_gain_difference_fails(self):
        expected = synthetic_expected(full=True)
        capture = clean_capture(expected)
        capture["status"][3] += 1
        rows = rv.voice_rows(capture, expected)
        self.assertIn("status.norm.gain", [row[0] for row in rows])

    def test_x_state_emission_is_a_mismatch(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        capture["audio1"][2] = None
        rows = rv.voice_rows(capture, expected)
        self.assertEqual(rows[0][2], 2)
        self.assertIsNone(rows[0][4])

    def test_op_counter_difference_is_reported(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        capture["ops"][("A", 3)][0] += 1
        rows = rv.voice_rows(capture, expected)
        self.assertIn("ops.A.3[0]", [row[0] for row in rows])

    def test_missing_op_record_is_reported(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        del capture["ops"][("U", 2, 4)]
        rows = rv.voice_rows(capture, expected)
        self.assertIn("ops.U.2.4", [row[0] for row in rows])

    def test_engine_pass_index_is_checked_on_full_walks(self):
        expected = synthetic_expected(full=True)
        capture = clean_capture(expected)
        capture["audio2"][0][2] = 1
        rows = rv.voice_rows(capture, expected)
        self.assertIn("pass_index[pass2]", [row[0] for row in rows])

    def test_short_audio_capture_is_a_mismatch(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        capture["audio1"] = capture["audio1"][:-1]
        rows = rv.voice_rows(capture, expected)
        self.assertTrue(rows)


class ComparedTracesCoverEveryDeclaredCheckpoint(unittest.TestCase):
    def test_trace_set_is_exactly_the_model_checkpoint_set(self):
        from torchsynth_voice.float_voice import voice_checkpoints

        compared = set(rv.VOICE_CTL_TRACES) | set(rv.VOICE_AUDIO_TRACES) | {
            "mixer.output", "mixer.peak", "mixer.gain",
        }
        host_fed = {"keyboard.midi_f0", "keyboard.duration"}
        self.assertEqual(set(voice_checkpoints()), compared | host_fed)
        self.assertFalse(compared & host_fed)

    def test_engine_internal_traces_are_not_model_checkpoints(self):
        # #263: the exported MIDI sum is an engine-internal mirror trace; it
        # must not silently join (or be confused with) the normative set.
        from torchsynth_voice.float_voice import voice_checkpoints

        aux = set(rv.VOICE_AUX_AUDIO_TRACES)
        self.assertEqual(aux, {"vco_1.midi_sum"})
        self.assertFalse(aux & set(voice_checkpoints()))
        self.assertFalse(aux & set(rv.VOICE_AUDIO_TRACES))

    def test_pitch_wire_swap_requires_both_kills(self):
        self.assertEqual(rv.VOICE_PITCH_WIRE_KILLS,
                         ("vco_1.midi_sum[pass1]", "ops.V1.1[5]"))

    def test_wrong_midi_sum_is_a_localized_trace_mismatch(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        col = 3 + len(rv.VOICE_AUDIO_TRACES)
        capture["audio1"][4][col] += 1
        rows = rv.voice_rows(capture, expected)
        self.assertEqual(rows[0][0], "vco_1.midi_sum[pass1]")
        self.assertEqual(rows[0][1], 10_004)
        self.assertEqual(rows[0][2], 4)

    def test_no_trace_is_compared_twice(self):
        names = list(rv.VOICE_CTL_TRACES) + list(rv.VOICE_AUDIO_TRACES)
        self.assertEqual(len(names), len(set(names)))


class UpsampleInteriorCountMatchesTheModel(unittest.TestCase):
    def test_closed_form_matches_the_model_coordinate_table(self):
        from torchsynth_voice.format_sweep import up_coordinate_table
        from torchsynth_voice.fixedpoint.rounding import RoundingMode

        table = up_coordinate_table(31, RoundingMode.HALF_EVEN)
        interior = sum(1 for _low, frac in table if frac != 0)
        self.assertEqual(rv.voice_interior_count(rv.AUDIO_SAMPLES), interior)

    def test_full_walk_interior_is_the_declared_176398(self):
        self.assertEqual(rv.voice_interior_count(rv.AUDIO_SAMPLES), 176398)

    def test_prefix_walk_interior_excludes_only_the_first_endpoint(self):
        self.assertEqual(
            rv.voice_interior_count(rv.VOICE_MUTATION_WALK_CAP),
            rv.VOICE_MUTATION_WALK_CAP - 1,
        )


class MutationSeamsAreAnchored(unittest.TestCase):
    def test_every_top_anchor_occurs_exactly_once(self):
        source = rv.VOICE_TOP_SV.read_text(encoding="utf-8")
        for label, (anchor, _mutant) in rv.VOICE_TOP_MUTATIONS.items():
            self.assertEqual(source.count(anchor), 1, label)

    def test_engine_anchors_occur_exactly_once(self):
        up = rv.MM_UP_DUT_SV.read_text(encoding="utf-8")
        self.assertEqual(up.count(rv.VOICE_ZOH_ANCHOR), 1)
        mixer = rv.MIX_DUT_SV.read_text(encoding="utf-8")
        self.assertEqual(
            mixer.count(rv.MIX_RTL_MUTATIONS["mixer-truncate"][0]), 1
        )
        replay = rv.NORMREPLAY_DUT_SV.read_text(encoding="utf-8")
        for anchor in (rv.NORMREPLAY_ALWAYS_ON_ANCHOR,
                       rv.NORMREPLAY_WRONG_RECIPROCAL_ANCHOR,
                       rv.NORMREPLAY_WRONG_PEAK_ANCHOR):
            self.assertEqual(replay.count(anchor), 1)

    def test_every_mutant_actually_changes_the_source(self):
        source = rv.VOICE_TOP_SV.read_text(encoding="utf-8")
        for label, (anchor, mutant) in rv.VOICE_TOP_MUTATIONS.items():
            self.assertNotEqual(anchor, mutant, label)
            self.assertNotEqual(
                source, source.replace(anchor, mutant), label
            )

    def test_missing_sample_mutant_still_declares_link_valid_once(self):
        anchor, mutant = rv.VOICE_TOP_MUTATIONS["missing-sample"]
        mutated = rv.VOICE_TOP_SV.read_text(encoding="utf-8").replace(
            anchor, mutant
        )
        self.assertEqual(mutated.count("wire link_valid = mix_out_valid"), 1)
        self.assertIn("drop_count == 32'd%d" % rv.VOICE_DROP_SAMPLE, mutated)

    @unittest.skipUnless(has_iverilog(), "iverilog is not installed")
    def test_top_and_every_mutant_elaborate(self):
        with tempfile.TemporaryDirectory(prefix="voice-elab-") as tmp:
            tmp = Path(tmp)
            variants = {"pristine": rv.VOICE_TOP_SV}
            for label, (anchor, mutant) in rv.VOICE_TOP_MUTATIONS.items():
                path = tmp / ("mutant_%s.sv" % label.replace("-", "_"))
                path.write_text(
                    rv.VOICE_TOP_SV.read_text(encoding="utf-8").replace(
                        anchor, mutant
                    ),
                    encoding="utf-8",
                )
                variants[label] = path
            for label, top in variants.items():
                files = [rv.CONSTANTS_PKG_SV] + list(rv.VOICE_ENGINE_SV) + [
                    top, rv.VOICE_TB_SV
                ]
                done = subprocess.run(
                    ["iverilog", "-g2012", "-o",
                     str(tmp / ("v_%s.vvp" % label.replace("-", "_")))]
                    + [str(f) for f in files],
                    capture_output=True, text=True,
                )
                self.assertEqual(done.returncode, 0,
                                 "%s: %s" % (label, done.stderr))


class BindingCaseBreaksEveryStimulusSymmetry(unittest.TestCase):
    """A binding fault is only observable against a stimulus that
    distinguishes the two things it confuses.

    ``special:stress`` is uniform -- all twenty modulation depths are 1.0,
    all six envelope parameter sets are identical and the two LFO sides are
    identical -- so ``matrix-column-shuffle``, ``lfo-envelope-swap``,
    ``amp-route-swap`` and the pitch-route controls all SURVIVE on it. The
    first run of this lane reported exactly those four as NOT DETECTED. These
    checks pin the asymmetry that kills them, so a future edit cannot quietly
    restore a degenerate stimulus and turn six controls back into no-ops.
    """

    @classmethod
    def setUpClass(cls):
        from run_tb import mix_load_fixtures

        _manifest, fixtures = mix_load_fixtures()
        base_id, overrides = rv.VOICE_DERIVED_CASES[
            rv.VOICE_MUTATION_BINDING_CASE
        ]
        cls.base = fixtures[base_id]["physical"]
        cls.physical = dict(cls.base)
        cls.physical.update(overrides)

    def test_the_base_fixture_really_is_degenerate(self):
        # If this ever stops holding, the overrides below may be unnecessary
        # -- but the check must then be revisited deliberately, not silently.
        depths = {k: v for k, v in self.base.items()
                  if k.startswith("mod_matrix.")}
        self.assertEqual(len(depths), 20)
        self.assertEqual(len(set(depths.values())), 1)

    def test_all_twenty_modulation_depths_are_distinct(self):
        depths = [v for k, v in self.physical.items()
                  if k.startswith("mod_matrix.")]
        self.assertEqual(len(depths), 20)
        self.assertEqual(len(set(depths)), 20)

    def test_the_two_pitch_rows_are_well_separated(self):
        # pitch-column-load-swap swaps the two pitch routes; a route pair that
        # differs only slightly can leave the swap below one output quantum.
        low = max(rv.VOICE_BINDING_DEPTHS["vco_1_pitch"])
        high = min(rv.VOICE_BINDING_DEPTHS["vco_2_pitch"])
        self.assertGreater(high - low, 0.5)

    def test_every_route_row_occupies_its_own_band(self):
        rows = list(rv.VOICE_BINDING_DEPTHS.values())
        for index, row in enumerate(rows):
            for other in rows[index + 1:]:
                self.assertFalse(
                    set(row) & set(other),
                    "two route rows share a depth word",
                )

    def test_all_six_envelope_formations_are_distinct(self):
        formations = set(rv.VOICE_BINDING_ENVELOPES.values())
        self.assertEqual(len(formations), 6)
        self.assertEqual(set(rv.VOICE_BINDING_ENVELOPES), {
            "adsr_1", "adsr_2", "lfo_1_rate_adsr", "lfo_1_amp_adsr",
            "lfo_2_rate_adsr", "lfo_2_amp_adsr",
        })

    def test_each_lfo_rate_and_amp_envelope_pair_differs(self):
        # lfo-envelope-swap swaps the rate and gain roles per side.
        for side in ("lfo_1", "lfo_2"):
            self.assertNotEqual(
                rv.VOICE_BINDING_ENVELOPES[side + "_rate_adsr"],
                rv.VOICE_BINDING_ENVELOPES[side + "_amp_adsr"],
                side,
            )

    def test_every_attack_is_inside_the_prefix_walk(self):
        # 6,000 audio samples == 60 control ticks at 441 Hz == ~0.136 s. An
        # envelope-role fault must diverge inside the cap, not only in the
        # release tail.
        seconds = rv.VOICE_MUTATION_WALK_CAP / 44100.0
        for prefix, formation in rv.VOICE_BINDING_ENVELOPES.items():
            self.assertLess(formation[0], seconds, prefix)

    def test_the_two_lfo_sides_differ(self):
        for field in ("frequency", "mod_depth", "initial_phase", "sin", "saw"):
            self.assertNotEqual(
                self.physical["lfo_1." + field],
                self.physical["lfo_2." + field],
                field,
            )

    def test_every_binding_control_is_a_declared_top_mutation(self):
        for label in rv.VOICE_BINDING_CONTROLS:
            self.assertIn(label, rv.VOICE_TOP_MUTATIONS, label)

    def test_binding_case_is_a_declared_derived_fixture(self):
        self.assertIn(
            rv.VOICE_MUTATION_BINDING_CASE, rv.VOICE_DERIVED_CASES
        )


class PrefixWalksStillCheckTheVcoOpCounters(unittest.TestCase):
    """``vco-pitch-wire-swap`` is observable ONLY on an op counter.

    The frequency both VCOs integrate is the host-replayed exp2 shadow word,
    so a wrong pitch column changes no sample trace -- only the engine's
    measured MIDI-clamp count. If a prefix-capped walk left that counter
    unchecked (expected ``None``), the control would be a silent no-op.
    """

    def _case(self, cap):
        traces = {name: [0] * CONTROL_TICKS for name in rv.VOICE_CTL_TRACES}
        traces.update({name: [0] * cap for name in rv.VOICE_AUDIO_TRACES})
        traces["mixer.output"] = [0] * cap
        return {
            "id": "synthetic",
            "traces": traces,
            "norm_diag": {"normalized_branch": False, "peak_word": 0,
                          "gain_word": 0},
            "norm_counters": type("C", (), {"total": lambda self: 0})(),
            "sine_aux": {"counters": {"records": []}, "clamps": 7},
            "sine_midi_sum": list(range(10_000)),
            "vco2_sats": 11,
            "prefix_aux": {cap: {"sine_sats": 3, "sine_clamps": 5,
                                 "vco2_sats": 9}},
            "lfo": [{"clamps": 0}, {"clamps": 0}],
            "matrix_sats": 0,
            "up_sats": {route: 0 for route in range(5)},
            "mix_truth": {"streams": {}, "aux": {"sats": 0, "rounds": 0}},
            "sound_index": 0,
        }

    def test_declared_prefix_cap_checks_sat_and_clamp_counters(self):
        cap = rv.VOICE_MUTATION_WALK_CAP
        ops = rv.voice_expected(self._case(cap), cap)["ops"]
        self.assertEqual(ops[("V1", 1)][4], 3)
        self.assertEqual(ops[("V1", 1)][5], 5)
        self.assertEqual(ops[("V2", 1)][3], 9)

    def test_full_walk_uses_the_whole_clip_tallies(self):
        case = self._case(rv.VOICE_MUTATION_WALK_CAP)
        case["traces"] = {
            name: [0] * CONTROL_TICKS for name in rv.VOICE_CTL_TRACES
        }
        case["traces"].update(
            {name: [0] * rv.AUDIO_SAMPLES for name in rv.VOICE_AUDIO_TRACES}
        )
        case["traces"]["mixer.output"] = [0] * rv.AUDIO_SAMPLES
        ops = rv.voice_expected(case, rv.AUDIO_SAMPLES)["ops"]
        self.assertEqual(ops[("V1", 1)][5], 7)
        self.assertEqual(ops[("V2", 1)][3], 11)

    def test_an_undeclared_walk_length_leaves_them_unchecked(self):
        case = self._case(rv.VOICE_MUTATION_WALK_CAP)
        case["traces"] = {
            name: [0] * CONTROL_TICKS for name in rv.VOICE_CTL_TRACES
        }
        case["traces"].update(
            {name: [0] * 64 for name in rv.VOICE_AUDIO_TRACES}
        )
        ops = rv.voice_expected(case, 64)["ops"]
        self.assertIsNone(ops[("V1", 1)][4])
        self.assertIsNone(ops[("V2", 1)][3])


class UpsampleSaturationCounterIsCheckedAtEveryWalkLength(unittest.TestCase):
    """``ops.U.*[5]`` must not be silently skipped (#254).

    Position 5 of each ``U`` op row is the upsample engine's sticky
    saturation counter (``upsample_engine.sv``'s ``op_sats``, the sixth
    ``U`` field the bench emits). It used to be expected ``None`` for every
    route, every pass and **every** walk length -- and ``voice_rows`` skips a
    ``None`` expectation -- so this lane never compared it at all, not even
    on the full-length committed cases. That is unlike the other ``None``s
    in :func:`run_voice.voice_expected`, which are walk-conditional and do
    get compared on a full walk.

    These checks pin that the position now carries the model's own per-route
    tally, that a capped walk still compares it whenever that is exact, and
    that a wrong count is actually reported as a mismatch.
    """

    def _case(self, up_sats, walk):
        cap = rv.VOICE_MUTATION_WALK_CAP
        traces = {name: [0] * CONTROL_TICKS for name in rv.VOICE_CTL_TRACES}
        traces.update({name: [0] * walk for name in rv.VOICE_AUDIO_TRACES})
        traces["mixer.output"] = [0] * walk
        return {
            "id": "synthetic",
            "traces": traces,
            "norm_diag": {"normalized_branch": False, "peak_word": 0,
                          "gain_word": 0},
            "norm_counters": type("C", (), {"total": lambda self: 0})(),
            "sine_aux": {"counters": {"records": []}, "clamps": 0},
            "sine_midi_sum": list(range(10_000)),
            "vco2_sats": 0,
            "prefix_aux": {cap: {"sine_sats": 0, "sine_clamps": 0,
                                 "vco2_sats": 0}},
            "lfo": [{"clamps": 0}, {"clamps": 0}],
            "matrix_sats": 0,
            "up_sats": dict(up_sats),
            "mix_truth": {"streams": {}, "aux": {"sats": 0, "rounds": 0}},
            "sound_index": 0,
        }

    def test_full_walk_compares_the_models_per_route_tally(self):
        # Distinct per-route values, so the route ordering is pinned too and
        # not merely "all zero happens to match".
        up_sats = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}
        ops = rv.voice_expected(
            self._case(up_sats, rv.AUDIO_SAMPLES), rv.AUDIO_SAMPLES)["ops"]
        for pas in (1, 2):
            for route in range(5):
                self.assertEqual(ops[("U", pas, route)][5], up_sats[route],
                                 (pas, route))

    def test_the_full_walk_expectation_is_never_none(self):
        # The regression this class exists for: a None here is invisible to
        # the comparator, so the counter would be unchecked on exactly the
        # runs the committed evidence records are made from.
        ops = rv.voice_expected(
            self._case({route: 0 for route in range(5)}, rv.AUDIO_SAMPLES),
            rv.AUDIO_SAMPLES)["ops"]
        for pas in (1, 2):
            for route in range(5):
                self.assertIsNotNone(ops[("U", pas, route)][5], (pas, route))

    def test_declared_prefix_cap_still_compares_a_zero_tally(self):
        # A zero full-clip tally forces zero on every prefix (the counter is
        # sticky and monotone in the walk length), so a capped walk is exact
        # here rather than unchecked.
        cap = rv.VOICE_MUTATION_WALK_CAP
        ops = rv.voice_expected(
            self._case({route: 0 for route in range(5)}, cap), cap)["ops"]
        for pas in (1, 2):
            for route in range(5):
                self.assertEqual(ops[("U", pas, route)][5], 0, (pas, route))

    def test_a_nonzero_tally_is_unchecked_only_on_a_capped_walk(self):
        # An aggregate count cannot be localized to a prefix, so a non-zero
        # tally is honestly left unchecked on a capped walk -- never guessed
        # -- while the full walk still compares it exactly.
        cap = rv.VOICE_MUTATION_WALK_CAP
        up_sats = {0: 0, 1: 0, 2: 7, 3: 0, 4: 0}
        capped = rv.voice_expected(self._case(up_sats, cap), cap)["ops"]
        self.assertIsNone(capped[("U", 1, 2)][5])
        self.assertEqual(capped[("U", 1, 0)][5], 0)
        full = rv.voice_expected(
            self._case(up_sats, rv.AUDIO_SAMPLES), rv.AUDIO_SAMPLES)["ops"]
        self.assertEqual(full[("U", 1, 2)][5], 7)

    def test_an_overcounted_saturation_is_reported_as_a_mismatch(self):
        # End to end through the comparator: before #254 this capture was
        # indistinguishable from a conforming one.
        expected = synthetic_expected(full=True)
        capture = clean_capture(expected)
        capture["ops"][("U", 2, 3)][5] += 1
        rows = rv.voice_rows(capture, expected)
        self.assertEqual([row[0] for row in rows], ["ops.U.2.3[5]"])
        self.assertEqual(rows[0][3:], (0, 1))


class UpsampleSaturationTallyIsTheModelsOwnValue(unittest.TestCase):
    """The compared tally comes from the model, not from a constant (#254).

    ``voice_derive_case`` must reach ``up_sats`` through the same model call
    the #72 module lane uses (``mod_matrix_golden.mirror_upsample`` over the
    case's own matrix column), so a model change moves this lane's
    expectation instead of leaving a baked-in number silently disagreeing
    with it. Re-derived here independently on one regression case, which is
    pure Python -- no simulator.
    """

    @classmethod
    def setUpClass(cls):
        from run_tb import mix_load_receipt
        from torchsynth_voice.format_sweep import FixedControlPath

        _receipt, cls.formats, cls.cases = mix_load_receipt()
        cls.fcp = FixedControlPath(cls.formats.control_spec)
        cls._saved = dict(rv._LUT_TABLES)
        rv._LUT_TABLES["audio"] = cls.formats.table
        rv._LUT_TABLES["control"] = cls.fcp.table
        cls.case_id = rv.VOICE_MUTATION_BYPASS_CASE
        frozen = cls.cases[cls.case_id]
        cls.case = rv.voice_derive_case(
            cls.formats, cls.fcp, cls.case_id, frozen["parameters"], None,
            frozen,
        )

    @classmethod
    def tearDownClass(cls):
        rv._LUT_TABLES.clear()
        rv._LUT_TABLES.update(cls._saved)

    def test_every_route_carries_a_tally(self):
        self.assertEqual(sorted(self.case["up_sats"]), list(range(5)))

    def test_each_tally_equals_an_independent_mirror_walk(self):
        from torchsynth_voice import mod_matrix_golden as mm

        traces = self.case["traces"]
        for index, route in enumerate(rv.MOD_MATRIX_OUTPUTS):
            stream, counters = mm.mirror_upsample(
                self.fcp, traces["mod_matrix." + route], route)
            self.assertEqual(stream, traces["control_upsample." + route],
                             route)
            self.assertEqual(self.case["up_sats"][index],
                             counters["total_saturation"], route)


class CasePartitionMatchesFrozenReceipt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        cls.cases = {row["id"]: row for row in cls.receipt["cases"]}

    def test_regression_cases_are_receipt_cases_or_declared_fixtures(self):
        from run_tb import MIX_FIXTURE_CASES

        for case_id in rv.VOICE_REGRESSION_CASES:
            self.assertTrue(
                case_id in self.cases
                or case_id in MIX_FIXTURE_CASES
                or case_id in rv.VOICE_DERIVED_CASES,
                case_id,
            )

    def test_derived_cases_name_a_declared_base_fixture(self):
        from run_tb import MIX_FIXTURE_CASES

        for _case_id, (base, overrides) in rv.VOICE_DERIVED_CASES.items():
            self.assertIn(base, MIX_FIXTURE_CASES)
            self.assertTrue(overrides)

    def test_bypass_mutation_case_bypasses_in_the_frozen_receipt(self):
        case = self.cases[rv.VOICE_MUTATION_BYPASS_CASE]
        self.assertFalse(case["branch"]["fixed"])

    def test_divide_case_is_a_derived_fixture_not_a_digest_custody_case(self):
        # The receipt's own divide-branch cases are digest-custody corpus
        # items whose physical parameters are never committed, so the divide
        # demonstration must come from a declared derived fixture.
        self.assertIn(rv.VOICE_MUTATION_DIVIDE_CASE, rv.VOICE_DERIVED_CASES)

    def test_hash_and_mutation_cases_are_in_the_regression_set(self):
        for case_id in (rv.VOICE_HASH_CASE, rv.VOICE_MUTATION_DIVIDE_CASE,
                        rv.VOICE_MUTATION_BYPASS_CASE):
            self.assertIn(case_id, rv.VOICE_REGRESSION_CASES)

    def test_regression_set_covers_both_normalization_branches(self):
        self.assertNotEqual(rv.VOICE_MUTATION_DIVIDE_CASE,
                            rv.VOICE_MUTATION_BYPASS_CASE)


class DirectedProfileIsASupersetOfRegression(unittest.TestCase):
    """The ``directed`` profile widens case coverage and drops nothing (#79).

    Issue #79 asks for the integrated top over "compact directed vectors
    **and** the selected regression corpus". The ``regression`` profile is
    only the second half; ``directed`` adds the first without weakening any
    stage, which is a property worth enforcing rather than trusting: a
    profile that added cases while quietly dropping the mutation lane or a
    determinism check would produce a record that reads stronger and proves
    less.
    """

    def test_directed_vectors_are_exactly_the_declared_directed_fixtures(self):
        from run_tb import MIX_FIXTURE_CASES

        self.assertEqual(rv.VOICE_DIRECTED_CASES, MIX_FIXTURE_CASES)

    def test_directed_profile_contains_every_regression_case(self):
        for case_id in rv.VOICE_REGRESSION_CASES:
            self.assertIn(case_id, rv.VOICE_DIRECTED_PROFILE_CASES)

    def test_directed_profile_is_strictly_larger_than_regression(self):
        self.assertGreater(len(rv.VOICE_DIRECTED_PROFILE_CASES),
                           len(rv.VOICE_REGRESSION_CASES))
        self.assertEqual(
            len(set(rv.VOICE_DIRECTED_PROFILE_CASES)),
            len(rv.VOICE_DIRECTED_PROFILE_CASES),
            "a case is planned twice in the directed profile",
        )

    def test_directed_vectors_are_not_already_regression_cases(self):
        # If they were, the profile would add nothing.
        self.assertFalse(
            set(rv.VOICE_DIRECTED_CASES) & set(rv.VOICE_REGRESSION_CASES)
        )

    def test_the_cli_accepts_the_directed_profile(self):
        import contextlib
        import io

        for profile in ("regression", "directed", "full"):
            with self.subTest(profile=profile):
                # --help exits 0 after parsing choices; an unknown choice
                # exits 2 from argparse before that.
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    with self.assertRaises(SystemExit) as raised:
                        rv.main(["--profile", profile, "--help"])
                self.assertEqual(raised.exception.code, 0)
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            with self.assertRaises(SystemExit) as raised:
                rv.main(["--profile", "not-a-profile"])
        self.assertEqual(raised.exception.code, 2)

    def test_every_directed_vector_is_a_declared_fixture_with_parameters(self):
        from run_tb import mix_load_fixtures

        _manifest, fixtures = mix_load_fixtures()
        for case_id in rv.VOICE_DIRECTED_CASES:
            with self.subTest(case=case_id):
                self.assertIn(case_id, fixtures)
                self.assertTrue(fixtures[case_id]["physical"])


class PeakEnvelopeEndsComeOnlyFromTheDirectedVectors(unittest.TestCase):
    """The **low** end of the peak envelope is reachable only via directed.

    This is the whole reason the ``directed`` profile exists, so it is
    asserted against the frozen fixtures rather than asserted in prose. The
    peak is a magnitude of the accepted Q2.21 audio word, so its ceiling is
    the magnitude of that format's own negative rail -- derived here, never a
    literal, for the same reason the flow derives it.

    Stated precisely, because the first draft of this suite overstated it and
    this check is what caught that: the regression pair **does** reach the
    ceiling (``voice:divide-distinct-levels`` saturates there). What it never
    reaches is a zero or near-zero peak. Only the low end is exclusive to the
    directed vectors, and only that is claimed.

    No simulator is needed: the peak word is a property of the frozen fixed
    model's own normalization diagnostic, which is pure Python.
    """

    @classmethod
    def setUpClass(cls):
        from run_tb import mix_load_fixtures, mix_load_receipt
        from torchsynth_voice.format_sweep import FixedControlPath

        _receipt, cls.formats, cls.cases = mix_load_receipt()
        _manifest, cls.fixtures = mix_load_fixtures()
        cls.fcp = FixedControlPath(cls.formats.control_spec)
        cls._saved = dict(rv._LUT_TABLES)
        rv._LUT_TABLES["audio"] = cls.formats.table
        rv._LUT_TABLES["control"] = cls.fcp.table

    @classmethod
    def tearDownClass(cls):
        rv._LUT_TABLES.clear()
        rv._LUT_TABLES.update(cls._saved)

    def _peak(self, case_id):
        if case_id in rv.VOICE_DERIVED_CASES:
            base_id, overrides = rv.VOICE_DERIVED_CASES[case_id]
            physical = dict(self.fixtures[base_id]["physical"])
            physical.update(overrides)
            normalized = self.fixtures[base_id]["normalized"]
            frozen = None
        elif case_id in self.fixtures:
            physical = self.fixtures[case_id]["physical"]
            normalized = self.fixtures[case_id]["normalized"]
            frozen = None
        else:
            frozen = self.cases[case_id]
            physical, normalized = frozen["parameters"], None
        case = rv.voice_derive_case(self.formats, self.fcp, case_id, physical,
                                    normalized, frozen)
        return case["norm_diag"]["peak_word"]

    def test_ceiling_is_the_audio_format_negative_rail_magnitude(self):
        self.assertEqual(-self.formats.audio.min_int, 1 << 23)

    def test_a_directed_vector_reaches_a_zero_peak(self):
        peaks = {cid: self._peak(cid) for cid in rv.VOICE_DIRECTED_CASES}
        self.assertIn(0, peaks.values(), peaks)

    def test_a_directed_vector_reaches_the_ceiling_peak(self):
        ceiling = -self.formats.audio.min_int
        peaks = {cid: self._peak(cid) for cid in rv.VOICE_DIRECTED_CASES}
        self.assertIn(ceiling, peaks.values(), peaks)

    def test_the_regression_cases_alone_never_reach_a_zero_peak(self):
        # The claim the directed profile is built on. If a future edit made
        # a regression case reach zero, this failing is the signal to
        # re-state the partition, not to delete the check.
        peaks = {cid: self._peak(cid) for cid in rv.VOICE_REGRESSION_CASES}
        self.assertNotIn(0, peaks.values(), peaks)

    def test_the_regression_cases_do_reach_the_ceiling(self):
        # Recorded, not glossed over: the ceiling end is NOT what the
        # directed vectors add. Asserting it here keeps the spec's claim
        # ("widens the parameters, not the peak range") honest.
        ceiling = -self.formats.audio.min_int
        peaks = {cid: self._peak(cid) for cid in rv.VOICE_REGRESSION_CASES}
        self.assertIn(ceiling, peaks.values(), peaks)

    def test_near_silence_is_the_smallest_non_zero_peak_either_profile_plans(
            self):
        planned = set(rv.VOICE_DIRECTED_PROFILE_CASES)
        peaks = {cid: self._peak(cid) for cid in planned}
        non_zero = {cid: p for cid, p in peaks.items() if p}
        self.assertEqual(min(non_zero.values()), peaks["special:near-silence"],
                         peaks)


class CorruptCaptureIsInconclusiveNotAVerdict(unittest.TestCase):
    """A capture with lost bytes must not be reported as a result (#79).

    ``CLAUDE.md``: a test that did not run must never be reported as a pass.
    The corollary this suite pins down is that it must not be reported as a
    *failure of the RTL* either. A row with the wrong field count can only
    come from bytes lost between the bench's fixed-column ``$fwrite`` and
    the file, so it is diagnosable -- and must be diagnosed, not folded into
    the bit-identity verdict.

    The corrupt row below is verbatim from the real occurrence: a
    ``directed``-profile run on a shared box lost 9,987 of 352,800
    ``audiocap`` rows in five spliced holes, on a case that had already
    written all 352,800 rows and compared bit-exact earlier in the same run.
    """

    GOOD = "1017235 2 2 0 0 0 0 0 1656376 -42602 708392 0 0 0 0\n"
    #: Note the doubled space: a 15-column row's prefix spliced onto a much
    #: later row's suffix, giving 17 fields.
    SPLICED = ("1017239 2 2 0 0 0  0 0 0 0 872194 -63157 -1090888 0 0 0 0\n")

    def _write(self, text):
        tmp = tempfile.mkdtemp(prefix="voice-capture-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = Path(tmp) / "run0_audiocap.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_well_formed_capture_parses(self):
        rows = rv.voice_capture_rows(self._write(self.GOOD * 3), "audiocap", 15)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0], 1017235)

    def test_a_spliced_row_raises_capture_integrity_not_a_mismatch(self):
        path = self._write(self.GOOD + self.SPLICED + self.GOOD)
        with self.assertRaises(rv.VoiceCaptureIntegrityError) as raised:
            rv.voice_capture_rows(path, "audiocap", 15)
        message = str(raised.exception)
        # The diagnosis must name the file, the line, both field counts, and
        # say plainly that this is not a bit-identity result.
        self.assertIn("CORRUPT", message)
        self.assertIn("line 2", message)
        self.assertIn("17 fields", message)
        self.assertIn(str(path), message)
        self.assertIn("not an RTL/model disagreement", message)

    def test_an_x_state_row_is_still_a_mismatch_not_a_corruption(self):
        # An x-state emission has the RIGHT number of fields and IS a
        # genuine mismatch; the comparator owns it. Conflating the two would
        # turn a real RTL defect into an "inconclusive" excuse.
        xrow = "1017239 2 2 0 0 0 0 0 x -39083 502598 0 0 0 0\n"
        rows = rv.voice_capture_rows(self._write(self.GOOD + xrow),
                                     "audiocap", 15)
        self.assertEqual(rows[1], None)

    def test_blank_lines_are_skipped_rather_than_called_corrupt(self):
        rows = rv.voice_capture_rows(self._write("\n" + self.GOOD + "\n"),
                                     "audiocap", 15)
        self.assertEqual(len(rows), 1)

    def test_the_flow_exits_4_and_writes_no_record_on_a_corrupt_capture(self):
        # Exit 4 is distinct from 0 (pass) and 1 (a verdict against the
        # RTL), for the same reason exit 3 is distinct for "no simulator".
        import contextlib
        import io

        tmp = tempfile.mkdtemp(prefix="voice-inconclusive-")
        self.addCleanup(shutil.rmtree, tmp, True)
        real_voice = rv.voice

        def fake_voice(workdir):
            raise rv.VoiceCaptureIntegrityError("capture audiocap is CORRUPT")

        rv.voice = fake_voice
        self.addCleanup(lambda: setattr(rv, "voice", real_voice))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status = rv.main(["--profile", "regression", "--workdir", tmp])
        if status == 3:
            self.skipTest("iverilog absent; the flow exits 3 before running")
        self.assertEqual(status, 4)
        self.assertIn("INCONCLUSIVE", buffer.getvalue())
        self.assertFalse((Path(tmp) / "voice-evidence.json").exists())

    def test_exit_statuses_are_documented_in_the_module_docstring(self):
        doc = rv.__doc__
        for fragment in ("``0`` pass", "``3``", "``4``", "INCONCLUSIVE"):
            self.assertIn(fragment, doc)


class LfoTableSelectionIsBackwardCompatible(unittest.TestCase):
    """The #71 engine reads the CONTROL path's table, not the audio one.

    The integrated top composes both lanes in one elaboration, so the two
    tables must be distinguishable. ``+ctl_lut`` is preferred and ``+lut``
    remains the fallback, so every pre-existing single-lane bench keeps
    working unchanged.
    """

    def test_engine_prefers_ctl_lut_and_falls_back_to_lut(self):
        source = rv.LFO_DUT_SV.read_text(encoding="utf-8")
        self.assertIn('$value$plusargs("ctl_lut=%s", lut_file)', source)
        self.assertIn('$value$plusargs("lut=%s", lut_file)', source)
        self.assertLess(source.index('"ctl_lut=%s"'), source.index('"lut=%s"'))

    def test_audio_lane_engines_still_read_the_bare_lut_plusarg(self):
        for path in (rv.VCO_DUT_SV, rv.VCO_LUT_SV):
            source = path.read_text(encoding="utf-8")
            self.assertIn('$value$plusargs("lut=%s"', source)
            self.assertNotIn("ctl_lut", source)

    def test_the_two_tables_really_differ(self):
        from torchsynth_voice.fixed_voice import AcceptedFormats
        from torchsynth_voice.format_sweep import FixedControlPath

        formats = AcceptedFormats()
        control = FixedControlPath(formats.control_spec)
        self.assertNotEqual(
            list(formats.table.entries), list(control.table.entries)
        )


class SimulatorPathsAreAbsolute(unittest.TestCase):
    """A relative ``--workdir`` must still work -- the CI lane passes one.

    Every simulator invocation runs with ``cwd=workdir`` (the bench opens its
    stimulus and capture files by bare name), so a relative path handed to
    ``iverilog -o`` resolves against the already-entered directory and fails
    with "No such file or directory". That is not hypothetical: the first CI
    run of the ``oneshot-whole-voice`` job failed in 34 s this way, while the
    local run passed because it used an absolute ``--workdir``. This check
    needs no simulator, so it runs on every PR in ``ci.yml``.
    """

    class _Captured(Exception):
        pass

    def _commands_for(self, workdir):
        from torchsynth_voice.fixed_voice import AcceptedFormats
        from torchsynth_voice.format_sweep import FixedControlPath

        formats = AcceptedFormats()
        saved_tables = dict(rv._LUT_TABLES)
        rv._LUT_TABLES["audio"] = formats.table
        rv._LUT_TABLES["control"] = FixedControlPath(formats.control_spec).table
        self.addCleanup(
            lambda: (rv._LUT_TABLES.clear(), rv._LUT_TABLES.update(saved_tables))
        )
        seen = []
        real_run = rv._run

        def fake_run(command, cwd=None, **kwargs):
            seen.append((list(command), cwd))
            raise SimulatorPathsAreAbsolute._Captured()

        rv._run = fake_run
        try:
            rv.voice_simulate(workdir, 1)
        except SimulatorPathsAreAbsolute._Captured:
            pass
        finally:
            rv._run = real_run
        return seen

    def test_relative_workdir_still_yields_an_absolute_output_path(self):
        with tempfile.TemporaryDirectory(prefix="voice-relpath-") as tmp:
            cwd = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                Path("out/whole-voice").mkdir(parents=True)
                seen = self._commands_for(Path("out/whole-voice"))
            finally:
                os.chdir(cwd)
        self.assertTrue(seen, "no simulator invocation was captured")
        command, _cwd = seen[0]
        self.assertEqual(command[0], "iverilog")
        out = command[command.index("-o") + 1]
        self.assertTrue(
            Path(out).is_absolute(),
            "iverilog -o path %r is relative; it will not resolve once the "
            "simulator has chdir'd into the work directory" % out,
        )

    def test_the_simulator_cwd_is_the_resolved_workdir(self):
        with tempfile.TemporaryDirectory(prefix="voice-relpath-") as tmp:
            cwd = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                Path("out/whole-voice").mkdir(parents=True)
                seen = self._commands_for(Path("out/whole-voice"))
            finally:
                os.chdir(cwd)
        _command, run_cwd = seen[0]
        self.assertTrue(Path(run_cwd).is_absolute())


class CiJobBudgetsAreConsistent(unittest.TestCase):
    WORKFLOW = ROOT / ".github/workflows/tb-sim.yml"

    @classmethod
    def setUpClass(cls):
        cls.text = cls.WORKFLOW.read_text(encoding="utf-8")

    def test_whole_voice_lane_has_its_own_job(self):
        self.assertIn("oneshot-whole-voice:", self.text)
        self.assertEqual(
            self.text.count("tb/run_voice.py --profile regression"), 1
        )

    def test_job_cap_is_within_the_hosted_limit(self):
        caps = self._budgets()
        self.assertTrue(caps["cap"])
        self.assertLessEqual(caps["cap"], 360)

    def test_lane_step_budget_fits_under_the_job_cap(self):
        caps = self._budgets()
        self.assertTrue(caps["steps"])
        self.assertLess(sum(caps["steps"]), caps["cap"])

    def test_lane_step_can_actually_fail_the_job(self):
        # A continue-on-error gate is not a gate: an end-to-end bit-identity
        # failure must fail the job.
        block = self._block()
        self.assertNotRegex(block, r"continue-on-error:\s*true")

    def _block(self) -> str:
        block = self.text.split("\n  oneshot-whole-voice:\n", 1)
        self.assertEqual(len(block), 2, "the whole-voice job header is missing")
        rest = block[1]
        # Stop at the next 2-space-indented job header, if any.
        for index, line in enumerate(rest.splitlines()):
            if line and not line.startswith("   ") and line.strip().endswith(":"):
                return "\n".join(rest.splitlines()[:index])
        return rest

    def _budgets(self) -> dict:
        cap, steps = None, []
        for line in self._block().splitlines():
            if not line.strip().startswith("timeout-minutes:"):
                continue
            indent = len(line) - len(line.lstrip())
            value = int(line.split(":", 1)[1].strip())
            if indent == 4:
                cap = value
            else:
                steps.append(value)
        return {"cap": cap, "steps": steps}


if __name__ == "__main__":
    unittest.main()
