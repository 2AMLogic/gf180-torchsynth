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
- that the declared case partition agrees with the frozen receipt;
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
            ops[("U", pas, route)] = [n - 1, n - 1, n - 1, n - 1, n, None, n]
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
            "vco2_sats": 11,
            "prefix_aux": {cap: {"sine_sats": 3, "sine_clamps": 5,
                                 "vco2_sats": 9}},
            "lfo": [{"clamps": 0}, {"clamps": 0}],
            "matrix_sats": 0,
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
