"""Integrated mixer -> replay-controller chain, harness-level checks (#79).

These checks run everywhere and need no simulator: they prove the comparison
machinery (first-mismatch localization by trace/cycle/sample, exact status
and counter checks), the mutation seams (every anchor occurs exactly once so
a drifted RTL refuses instead of silently testing nothing), the stimulus
negative controls, and the declared case partition's consistency with the
frozen receipt. The full simulation flow (``tb/run_oneshot.py``) takes tens of
minutes and is arbitrated by CI / the remote box; it is NOT run by this
suite, and this suite passing is never evidence that it passed. The one
elaboration test below is skipped, not passed, where Icarus is absent.
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

import run_oneshot as ro  # noqa: E402

RECEIPT = ROOT / "sim/reference/fixed-voice-golden-v1.json"


def has_iverilog() -> bool:
    return shutil.which("iverilog") is not None


def synthetic_expected(n=8):
    mix = [(-1) ** i * (i + 1) for i in range(n)]
    return {
        "mix": mix,
        "out": [w * 2 for w in mix],
        "diag": {},
        "status": [0, 0, 8, 4194304, 1, 1, 3, n, n, 1, n, n, 0, 8],
        "mix_ops": [6 * n, 2 * n, 4 * n, 0, 0, n, 0],
    }


def clean_capture(expected):
    n = len(expected["mix"])
    return {
        "mix1": [(10 + i, w) for i, w in enumerate(expected["mix"])],
        "mix2": [(100 + i, w) for i, w in enumerate(expected["mix"])],
        "link1": [(10 + i, w) for i, w in enumerate(expected["mix"])],
        "link2": [(100 + i, w) for i, w in enumerate(expected["mix"])],
        "out": [(200 + i, w) for i, w in enumerate(expected["out"])],
        "status": list(expected["status"]),
        "ops": {"P1": list(expected["mix_ops"]), "P2": list(expected["mix_ops"])},
    }


class ComparatorLocalizesFirstMismatch(unittest.TestCase):
    def test_clean_capture_has_no_rows(self):
        expected = synthetic_expected()
        self.assertEqual(ro.oneshot_rows(clean_capture(expected), expected), [])

    def test_status_names_match_status_width(self):
        self.assertEqual(
            len(ro.ONESHOT_STATUS_NAMES), len(synthetic_expected()["status"])
        )

    def test_wrong_output_word_names_trace_cycle_sample(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        capture["out"][5] = (205, capture["out"][5][1] + 1)
        rows = ro.oneshot_rows(capture, expected)
        self.assertEqual(len(rows), 1)
        trace, cycle, sample, want, got = rows[0]
        self.assertEqual((trace, cycle, sample), ("mixer.output", 205, 5))
        self.assertEqual(got, want + 1)

    def test_missing_sample_is_localized_and_length_checked(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        del capture["mix1"][3]
        rows = ro.oneshot_rows(capture, expected)
        traces = {row[0]: row for row in rows}
        row = traces["mixer.pre_normalization[pass1]"]
        self.assertEqual(row[2], 3)  # first differing sample index

    def test_link_drop_is_localized_to_the_dropped_sample(self):
        # the #79 missing-sample mutant: the mixer emits every word but the
        # replay engine never consumes sample 3. The mixer trace is clean;
        # the link capture must name sample 3 as the earliest divergence.
        expected = synthetic_expected()
        capture = clean_capture(expected)
        del capture["link1"][3]
        rows = ro.oneshot_rows(capture, expected)
        self.assertEqual(rows[0][:3], ("link.replay_input[pass1]", 14, 3))

    def test_sequence_rows_are_ordered_by_cycle(self):
        # a late pass-1 mixer divergence must not mask an earlier link one
        expected = synthetic_expected()
        capture = clean_capture(expected)
        capture["mix1"].append((18, 99))          # extra late pass-1 word
        capture["link1"][2] = (12, capture["link1"][2][1] + 1)
        rows = ro.oneshot_rows(capture, expected)
        self.assertEqual(
            [r[0] for r in rows[:2]],
            ["link.replay_input[pass1]", "mixer.pre_normalization[pass1]"],
        )
        self.assertEqual([r[1] for r in rows[:2]], [12, 18])

    def test_missing_sample_location_is_the_declared_drop(self):
        self.assertEqual(
            ro.ONESHOT_MISSING_SAMPLE_LOCATION,
            ("link.replay_input[pass1]", ro.ONESHOT_DROP_SAMPLE),
        )
        self.assertIn("linkcap", ro.ONESHOT_ARTIFACTS)

    def test_no_tolerance_one_ulp_status_difference_fails(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        capture["status"][3] += 1  # gain word one ULP off
        rows = ro.oneshot_rows(capture, expected)
        self.assertEqual([r[0] for r in rows], ["status.norm.gain"])

    def test_x_state_emission_is_a_mismatch(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        capture["mix2"][0] = (None, None)
        self.assertTrue(ro.oneshot_rows(capture, expected))

    def test_both_pass_counters_are_checked(self):
        expected = synthetic_expected()
        capture = clean_capture(expected)
        capture["ops"]["P2"][0] += 1
        rows = ro.oneshot_rows(capture, expected)
        self.assertEqual([r[0] for r in rows], ["mixer.ops[P2]"])


class MutationSeamsAreAnchored(unittest.TestCase):
    def _count(self, path, anchor):
        return path.read_text(encoding="utf-8").count(anchor)

    def test_every_rtl_anchor_occurs_exactly_once(self):
        seams = (
            (ro.NORMREPLAY_DUT_SV, ro.NORMREPLAY_ALWAYS_ON_ANCHOR),
            (ro.NORMREPLAY_DUT_SV, ro.NORMREPLAY_WRONG_PEAK_ANCHOR),
            (ro.NORMREPLAY_DUT_SV, ro.NORMREPLAY_WRONG_RECIPROCAL_ANCHOR),
            (ro.ONESHOT_TOP_SV, ro.ONESHOT_LINK_ANCHOR),
            (ro.MIX_DUT_SV, ro.MIX_RTL_MUTATIONS["mixer-truncate"][0]),
        )
        for path, anchor in seams:
            with self.subTest(path=path.name, anchor=anchor[:40]):
                self.assertEqual(self._count(path, anchor), 1)

    def test_missing_sample_mutant_still_declares_link_valid_once(self):
        mutated = ro.mutate_sv(
            ro.ONESHOT_TOP_SV.read_text(encoding="utf-8"),
            ro.ONESHOT_LINK_ANCHOR,
            ro.ONESHOT_LINK_MISSING_SAMPLE,
            "missing-sample",
        )
        self.assertEqual(mutated.count("wire link_valid"), 1)
        self.assertIn("MUTANT", mutated)

    @unittest.skipUnless(has_iverilog(), "Icarus Verilog not installed")
    def test_top_and_missing_sample_mutant_elaborate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            mutant = tmp / "mutant_top.sv"
            mutant.write_text(
                ro.mutate_sv(
                    ro.ONESHOT_TOP_SV.read_text(encoding="utf-8"),
                    ro.ONESHOT_LINK_ANCHOR,
                    ro.ONESHOT_LINK_MISSING_SAMPLE,
                    "missing-sample",
                ),
                encoding="utf-8",
            )
            for top in (ro.ONESHOT_TOP_SV, mutant):
                subprocess.run(
                    [
                        "iverilog", "-g2012", "-o", str(tmp / "x.vvp"),
                        str(ro.CONSTANTS_PKG_SV), str(ro.MIX_DUT_SV),
                        str(ro.NORMREPLAY_DUT_SV), str(top),
                        str(ro.ONESHOT_TB_SV),
                    ],
                    check=True,
                )


class StimulusNegativeControls(unittest.TestCase):
    def _case(self):
        n = 400
        return {
            "id": "synthetic",
            "levels": {"vco_1": 5, "vco_2": 7, "noise": 11},
            "raw": {
                "vco_1": list(range(n)),
                "vco_2": list(range(n)),
                "noise": [(i * 37) % 101 for i in range(n)],
            },
            "amp": {
                lane: [i * 3 + k for i in range(n)]
                for k, lane in enumerate(("vco_1", "vco_2", "noise"))
            },
        }

    def test_each_control_changes_its_target_only(self):
        case = self._case()
        controls = ro.oneshot_stimulus_mutations(case)
        self.assertEqual(
            set(controls),
            {"parameter-shuffle", "wrong-noise", "interpolation-zoh",
             "gain-one-ulp"},
        )
        shuffled = controls["parameter-shuffle"]["levels"]
        self.assertNotEqual(shuffled, case["levels"])
        self.assertEqual(sorted(shuffled.values()), sorted(case["levels"].values()))
        noise = controls["wrong-noise"]["raw"]
        self.assertNotEqual(noise["noise"], case["raw"]["noise"])
        self.assertEqual(sorted(noise["noise"]), sorted(case["raw"]["noise"]))
        self.assertEqual(noise["vco_1"], case["raw"]["vco_1"])
        held = controls["interpolation-zoh"]["amp"]
        for lane in held:
            self.assertEqual(held[lane][99], held[lane][0])
            self.assertNotEqual(held[lane], case["amp"][lane])
        self.assertEqual(
            controls["gain-one-ulp"]["levels"]["noise"],
            case["levels"]["noise"] + 1,
        )

    def test_shuffle_refuses_a_case_with_repeated_levels(self):
        case = self._case()
        case["levels"] = {"vco_1": 5, "vco_2": 5, "noise": 11}
        with self.assertRaises(SystemExit):
            ro.oneshot_stimulus_mutations(case)


class CasePartitionMatchesFrozenReceipt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {
            c["id"]: c
            for c in json.loads(RECEIPT.read_text(encoding="utf-8"))["cases"]
        }

    def test_regression_cases_are_receipt_cases_or_declared_fixtures(self):
        for cid in ro.ONESHOT_REGRESSION_CASES:
            with self.subTest(case=cid):
                self.assertTrue(
                    cid in self.cases or cid in ro.MIX_FIXTURE_CASES
                    or cid in ro.ONESHOT_DERIVED_CASES
                )

    def test_derived_cases_name_a_declared_base_fixture(self):
        for cid, (base, overrides) in ro.ONESHOT_DERIVED_CASES.items():
            with self.subTest(case=cid):
                self.assertIn(base, ro.MIX_FIXTURE_CASES)
                self.assertTrue(overrides)
                self.assertNotIn(cid, self.cases)

    def test_bypass_mutation_case_bypasses_in_the_frozen_receipt(self):
        self.assertFalse(
            self.cases[ro.ONESHOT_MUTATION_BYPASS_CASE]["branch"]["fixed"]
        )

    def test_regression_set_names_divide_capable_fixtures(self):
        # the receipt's own divide-branch cases are the digest-custody
        # global-* corpus items, whose parameters are not committed; the
        # divide branch is therefore carried by directed/derived fixtures.
        for cid, case in self.cases.items():
            if cid.startswith("global-"):
                self.assertIsNone(case.get("parameters"))
        self.assertIn("special:stress", ro.ONESHOT_REGRESSION_CASES)
        self.assertIn(
            ro.ONESHOT_MUTATION_DIVIDE_CASE, ro.ONESHOT_DERIVED_CASES
        )

    def test_hash_and_mutation_cases_are_in_the_regression_set(self):
        for cid in set(ro.ONESHOT_HASH_CASES) | {
            ro.ONESHOT_MUTATION_DIVIDE_CASE,
            ro.ONESHOT_MUTATION_BYPASS_CASE,
        }:
            self.assertIn(cid, ro.ONESHOT_REGRESSION_CASES)


class CiJobBudgetsAreConsistent(unittest.TestCase):
    """tb-sim.yml: a capped job's step budgets must fit under its cap.

    Otherwise a slow lane is killed by the job cap instead of its own step
    timeout, and GitHub-hosted jobs hard-stop at 360 minutes regardless.
    Line-based (stdlib only; CI's unittest env need not carry PyYAML).
    """

    WORKFLOW = ROOT / ".github/workflows/tb-sim.yml"
    HOSTED_JOB_LIMIT = 360

    def _budgets(self):
        jobs, job, in_jobs = {}, None, False
        for line in self.WORKFLOW.read_text(encoding="utf-8").splitlines():
            if line.startswith("jobs:"):
                in_jobs = True
                continue
            if not in_jobs or not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            text = line.strip()
            if indent == 2 and text.endswith(":"):
                job = text[:-1]
                jobs[job] = {"cap": None, "steps": []}
            elif job and text.startswith("timeout-minutes:"):
                value = int(text.split(":", 1)[1])
                if indent == 4:
                    jobs[job]["cap"] = value
                else:
                    jobs[job]["steps"].append(value)
        return jobs

    def test_oneshot_lane_has_its_own_job(self):
        jobs = self._budgets()
        self.assertIn("oneshot-tail-chain", jobs)
        text = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count("tb/run_oneshot.py --profile regression"), 1)

    def test_step_budgets_fit_under_each_job_cap(self):
        # Every job must declare a cap (#265): an absent cap is no longer
        # skipped here. The stricter workflow-wide guard -- explicit budgets
        # on every setup/artifact step, required headroom, negative
        # fixtures -- is TestWorkflowBudgetsFitTheHostedLimit in
        # tests/test_ci_lane_wiring.py; this is the one-shot lane's own
        # coarse cross-check of the same property.
        for name, job in self._budgets().items():
            with self.subTest(job=name):
                self.assertIsNotNone(job["cap"], "%s has no job cap" % name)
                self.assertLess(sum(job["steps"]), job["cap"])

    def test_every_job_cap_is_within_the_hosted_limit(self):
        # Workflow-wide since #265 split the over-limit sim-lanes job
        # (formerly a 400-min cap over a 370-min step sum) into
        # sim-lanes-vco and sim-lanes-engines.
        for name, job in self._budgets().items():
            with self.subTest(job=name):
                self.assertIsNotNone(job["cap"], "%s has no job cap" % name)
                self.assertLess(job["cap"], self.HOSTED_JOB_LIMIT)

    def test_oneshot_job_cap_is_within_the_hosted_limit(self):
        job = self._budgets()["oneshot-tail-chain"]
        self.assertLess(job["cap"], self.HOSTED_JOB_LIMIT)


class SimulatorPathsAreAbsolute(unittest.TestCase):
    """A relative ``--workdir`` must still reach the simulator as absolute.

    Every simulator invocation runs with ``cwd=workdir`` (the bench opens its
    stimulus and capture files by bare name), so a relative path handed to
    ``iverilog -o`` resolves against the already-entered directory and fails
    with "No such file or directory". PR #253 fixed exactly that in
    ``run_voice.py``'s ``voice_simulate`` and pinned it with the equivalent
    pair in ``tests/test_oneshot_voice.py``; PR #259 backported the fix to
    ``run_oneshot.py`` but not the pinning tests (#260). The
    ``oneshot-tail-chain`` CI job always passes an absolute work directory (a
    ``tempfile.TemporaryDirectory``), so only these checks can catch a
    regression. They need no simulator, so they run on every PR in ``ci.yml``.
    """

    class _Captured(Exception):
        pass

    def _commands_for(self, workdir):
        seen = []
        real_run = ro._run

        def fake_run(command, cwd=None, **kwargs):
            seen.append((list(command), cwd))
            raise SimulatorPathsAreAbsolute._Captured()

        ro._run = fake_run
        try:
            ro.oneshot_simulate(workdir, 1)
        except SimulatorPathsAreAbsolute._Captured:
            pass
        finally:
            ro._run = real_run
        return seen

    def _commands_from_relative_workdir(self):
        with tempfile.TemporaryDirectory(prefix="oneshot-relpath-") as tmp:
            cwd = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                relative = Path("out/oneshot")
                relative.mkdir(parents=True)
                self.assertFalse(relative.is_absolute())
                return self._commands_for(relative)
            finally:
                os.chdir(cwd)

    def test_relative_workdir_still_yields_an_absolute_output_path(self):
        seen = self._commands_from_relative_workdir()
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
        seen = self._commands_from_relative_workdir()
        self.assertTrue(seen, "no simulator invocation was captured")
        _command, run_cwd = seen[0]
        self.assertTrue(Path(run_cwd).is_absolute())

    def test_main_resolves_a_relative_workdir_before_running(self):
        # main() must not hand its own relative --workdir down the chain:
        # printed artifact paths stay meaningful only if they survive the
        # simulator's chdir. Captured without a simulator by standing in for
        # both the iverilog probe and oneshot() itself.
        seen = []

        class _Shutil:
            def __getattr__(self, name):
                return getattr(shutil, name)

            @staticmethod
            def which(_name):
                return "/nonexistent/iverilog"

        real_shutil, real_oneshot = ro.shutil, ro.oneshot
        ro.shutil = _Shutil()
        ro.oneshot = lambda workdir, simulator: seen.append(workdir) or 0
        self.addCleanup(
            lambda: (setattr(ro, "shutil", real_shutil),
                     setattr(ro, "oneshot", real_oneshot))
        )
        with tempfile.TemporaryDirectory(prefix="oneshot-relpath-") as tmp:
            cwd = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                status = ro.main(["--workdir", "out/oneshot"])
            finally:
                os.chdir(cwd)
        self.assertEqual(status, 0)
        self.assertEqual(len(seen), 1)
        self.assertTrue(
            seen[0].is_absolute(),
            "main() passed a relative workdir %r to oneshot()" % (seen[0],),
        )


if __name__ == "__main__":
    unittest.main()
