"""Registered evaluator for the rtl-module-qualification-v1 check (issue #78).

This is the aggregation/gating layer's own test suite, not a second copy of
#69-#77's per-module conformance flows. It proves four things:

1. The gate's **inventory** cannot drift: the lane list is re-derived from
   ``tb/run_tb.py``'s own ``choices=[...]`` and every lane must carry lint
   units, declared coverage obligations and a row in the policy document.
2. The gate's **classifier** puts real linter and simulator output into the
   right classes, including the summary lines that are not diagnostics.
3. The gate **can fail**: a planted width mismatch in a copy of one engine,
   and a planted runtime diagnostic in a synthetic transcript, are both
   reported as failures. A ratchet nobody has seen trip is a decoration.
4. The **record** it writes validates against
   ``spec/schemas/capability-evidence-v1.schema.json`` and is honest about
   not being attachable while its prerequisite has no evidence.

It must run, and pass, in a tree containing only this check's declared covered
inputs plus ``capabilities.EVALUATOR_INPUTS`` -- that is what
``tests/test_capabilities.py``'s
``test_registered_checks_run_with_only_their_covered_inputs`` asserts. Every
test that needs the ``tb/sv`` tree or an installed linter is therefore
explicitly skipped when it is absent, and nothing here simulates.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import qualify_rtl_modules as q  # noqa: E402
from torchsynth_voice.capabilities import (  # noqa: E402
    CHECKS,
    _validate,
    coverage_hashes,
    load_graph,
)

CHECK_NAME = "rtl-module-qualification-v1"
SV_TREE = ROOT / "tb" / "sv"
SIM_REFERENCE = ROOT / "sim" / "reference"
GRAPH = ROOT / "spec" / "capabilities-v1.json"
POLICY = ROOT / q.POLICY_DOC
BASELINE = ROOT / q.BASELINE_PATH


def has_verilator() -> bool:
    return shutil.which("verilator") is not None


#: A real ``verilator --lint-only -Wall`` excerpt: two width diagnostics, one
#: excluded style class, and the codeless "%Error: Exiting due to" summary the
#: parser must NOT treat as a diagnostic.
VERILATOR_SAMPLE = """\
%Warning-WIDTHTRUNC: tb/sv/adsr_engine.sv:84:30: Operator VAR 'SHAPE_ONE' \
expects 63 bits on the Initial value, but Initial value's SHIFTL generates 64 bits.
                                         : ... note: In instance 'adsr_engine'
   84 |     localparam signed [62:0] SHAPE_ONE = 64'sd1 << 30;
      |                              ^~~~~~~~~
%Warning-WIDTHEXPAND: tb/sv/adsr_engine.sv:249:28: Operator ASSIGNW expects \
96 bits on the Assign RHS, but Assign RHS's SIGNED generates 32 bits.
%Warning-UNUSEDPARAM: tb/sv/gf180_rtl_constants_pkg.sv:9:20: Parameter is not used
%Error: Exiting due to 3 warning(s)
"""

#: A real ``iverilog -g2012 -Wall`` excerpt: the constants package's timescale
#: report (an excluded class) and patch_control's whole-array sensitivity.
IVERILOG_SAMPLE = """\
warning: Some design elements have no explicit time unit and/or
       : time precision. This may cause confusing timing results.
tb/sv/patch_control.sv:212: warning: @* is sensitive to all 64 words in \
array 'active_bank'.
"""


class InventoryTests(unittest.TestCase):
    """The gate cannot silently omit a lane, an axis or a lint unit."""

    def test_lane_list_matches_run_tb_choices(self):
        harness = (ROOT / "tb" / "run_tb.py").read_text(encoding="utf-8")
        match = re.search(r"choices=\[([^\]]*)\]", harness)
        self.assertIsNotNone(match, "tb/run_tb.py no longer declares choices=[...]")
        declared = tuple(re.findall(r'"([a-z0-9]+)"', match.group(1)))
        self.assertEqual(
            declared,
            q.LANES,
            "tb/run_tb.py's lane choices drifted from qualify_rtl_modules.LANES; "
            "a new lane must be added to the gate, not left outside it",
        )

    def test_every_lane_has_lint_units_and_obligations(self):
        for lane in q.LANES:
            with self.subTest(lane=lane):
                self.assertIn(lane, q.COVERAGE_MODEL)
                self.assertIn(lane, q.LANE_LINT_UNITS)
                for unit in q.LANE_LINT_UNITS[lane]:
                    self.assertIn(unit, q.LINT_UNITS)
                for axis in q.COVERAGE_MODEL[lane].axes:
                    self.assertIn(axis, q.AXES)

    def test_every_lint_unit_is_reachable_from_some_lane(self):
        reachable = {
            unit for lane in q.LANES for unit in q.LANE_LINT_UNITS[lane]
        }
        self.assertEqual(reachable, set(q.LINT_UNITS))

    def test_every_declared_axis_is_claimed(self):
        claimed = {
            axis
            for obligations in q.COVERAGE_MODEL.values()
            for axis in obligations.axes
        }
        self.assertEqual(
            claimed,
            set(q.AXES),
            "an acceptance-criteria axis with no lane carrying it is an "
            "unproven claim",
        )

    def test_fast_lanes_are_a_proper_bounded_subset(self):
        self.assertTrue(set(q.FAST_LANES) < set(q.LANES))
        self.assertEqual(q.resolve_lanes("all"), list(q.LANES))
        self.assertEqual(q.resolve_lanes("fast"), list(q.FAST_LANES))
        self.assertEqual(q.resolve_lanes("none"), [])
        self.assertEqual(q.resolve_lanes("vco,vco2,vco"), ["vco", "vco2"])
        with self.assertRaises(SystemExit):
            q.resolve_lanes("nosuchlane")

    def test_policy_document_names_every_lane_and_excluded_class(self):
        text = POLICY.read_text(encoding="utf-8")
        for lane in q.LANES:
            self.assertIn("`%s`" % lane, text, "policy omits lane " + lane)
        for name in q.EXCLUDED_CLASSES:
            self.assertIn(name, text, "policy omits excluded class " + name)
        for name in q.COVERAGE_EXCLUSIONS:
            self.assertIn(
                name.split("-")[0], text, "policy omits exclusion " + name
            )

    def test_every_excluded_class_states_a_reason(self):
        for name, reason in q.EXCLUDED_CLASSES.items():
            with self.subTest(name=name):
                self.assertGreater(len(reason), 30)
        for name, reason in q.COVERAGE_EXCLUSIONS.items():
            with self.subTest(name=name):
                self.assertGreater(len(reason), 60)

    @unittest.skipUnless(SV_TREE.is_dir(), "tb/sv is not present in this tree")
    def test_lint_units_name_existing_sources(self):
        for unit, sources in q.LINT_UNITS.items():
            for name in sources:
                with self.subTest(unit=unit, source=name):
                    self.assertTrue((SV_TREE / name).is_file())


class ClassifierTests(unittest.TestCase):
    def test_verilator_width_classes_and_summary_line(self):
        found = q.parse_verilator(VERILATOR_SAMPLE, ROOT, "adsr")
        codes = sorted(d.code for d in found)
        self.assertEqual(codes, ["UNUSEDPARAM", "WIDTHEXPAND", "WIDTHTRUNC"])
        self.assertNotIn(
            "VERILATOR-ERROR",
            codes,
            "'Exiting due to N warning(s)' is a run summary, not a diagnostic",
        )
        width = [d for d in found if d.code.startswith("WIDTH")]
        for diagnostic in width:
            self.assertEqual(diagnostic.where, "tb/sv/adsr_engine.sv")
            self.assertFalse(diagnostic.excluded, "width diagnostics must gate")
        self.assertTrue(
            any(d.excluded for d in found), "UNUSEDPARAM is an excluded class"
        )

    def test_codeless_verilator_error_is_a_gated_diagnostic(self):
        found = q.parse_verilator("%Error: Cannot open file foo.sv\n", ROOT, "adsr")
        self.assertEqual([d.code for d in found], ["VERILATOR-ERROR"])
        self.assertFalse(found[0].excluded)

    def test_iverilog_timescale_and_sensitivity_classes(self):
        found = q.parse_iverilog(IVERILOG_SAMPLE, ROOT, "patch")
        codes = sorted(d.code for d in found)
        self.assertEqual(codes, ["IVERILOG-SENSITIVITY", "IVERILOG-TIMESCALE"])
        by_code = {d.code: d for d in found}
        self.assertTrue(by_code["IVERILOG-TIMESCALE"].excluded)
        # Whole-array @* sensitivity is NOT class-excluded: it gates and is
        # waived by count in the ledger, which is the stronger disposition.
        self.assertFalse(by_code["IVERILOG-SENSITIVITY"].excluded)
        self.assertIn("IVERILOG-SENSITIVITY", q.WAIVER_REASONS)
        self.assertEqual(
            by_code["IVERILOG-SENSITIVITY"].where, "tb/sv/patch_control.sv"
        )

    def test_unclassified_iverilog_warning_gates(self):
        found = q.parse_iverilog(
            "tb/sv/x.sv:3: warning: something entirely new\n", ROOT, "adsr"
        )
        self.assertEqual([d.code for d in found], ["IVERILOG-OTHER"])
        self.assertFalse(
            found[0].excluded,
            "an unrecognised warning must gate, not be silently dropped",
        )

    def test_messages_are_normalised_into_stable_fingerprints(self):
        first = q.normalise_message(
            "/tmp/tb-adsr-abc/case-x/adsr_engine.vvp expects 63 bits"
        )
        second = q.normalise_message(
            "/tmp/tb-adsr-zzz/case-y/adsr_engine.vvp expects 12 bits"
        )
        self.assertEqual(first, second)

    def test_transcript_scan_skips_command_echo(self):
        transcript = (
            "+ iverilog -g2012 -o /tmp/warning-ish/path/x.vvp a.sv\n"
            "TB-WARN inst 0 out_valid low at tick 1763\n"
            "case attack-sweep: RTL sample-exact -> OK\n"
        )
        found = q.scan_transcript("adsr", transcript)
        self.assertEqual([d.code for d in found], ["TB-WARN"])
        self.assertEqual(found[0].where, "adsr")

    def test_transcript_scan_finds_simulator_diagnostics(self):
        transcript = (
            "vvp: warning: $readmemh: Not enough words in the file\n"
            "ERROR: engine refused the trigger\n"
            "signal is ambiguous at tick 12\n"
        )
        codes = sorted(d.code for d in q.scan_transcript("mix", transcript))
        self.assertEqual(codes, ["SIM-ERROR", "SIM-WARNING", "XZ-STATE"])


class LedgerTests(unittest.TestCase):
    """The ratchet: unwaived or increased diagnostics fail; improvements note."""

    def _diagnostic(self, unit, code="WIDTHTRUNC", where="tb/sv/a.sv"):
        return q.Diagnostic("lint", "verilator", where, code, "msg", unit)

    def test_tally_takes_worst_unit_not_sum(self):
        diagnostics = [self._diagnostic("patch") for _ in range(3)]
        diagnostics += [self._diagnostic("normreplay-binding") for _ in range(2)]
        tallied = q.tally(diagnostics)
        key = "verilator|tb/sv/a.sv|WIDTHTRUNC"
        self.assertEqual(tallied[key]["count"], 3)
        self.assertEqual(
            sorted(tallied[key]["units"]), ["normreplay-binding", "patch"]
        )

    def test_excluded_classes_never_enter_the_tally(self):
        self.assertEqual(q.tally([self._diagnostic("adsr", "UNUSEDPARAM")]), {})

    def test_unwaived_key_fails(self):
        observed = q.tally([self._diagnostic("adsr")])
        failures, _ = q.gate_diagnostics(observed, {"waivers": {}}, tiers=("lint",))
        self.assertEqual(len(failures), 1)
        self.assertIn("unwaived diagnostic", failures[0])

    def test_increase_over_waived_count_fails(self):
        observed = q.tally([self._diagnostic("adsr") for _ in range(4)])
        baseline = {
            "waivers": {
                "verilator|tb/sv/a.sv|WIDTHTRUNC": {
                    "count": 3, "tier": "lint", "reason": "declared arithmetic"
                }
            }
        }
        failures, _ = q.gate_diagnostics(observed, baseline, tiers=("lint",))
        self.assertEqual(len(failures), 1)
        self.assertIn("increased to 4", failures[0])

    def test_exact_count_passes_and_improvement_only_notes(self):
        baseline = {
            "waivers": {
                "verilator|tb/sv/a.sv|WIDTHTRUNC": {
                    "count": 3, "tier": "lint", "reason": "declared arithmetic"
                }
            }
        }
        exact = q.tally([self._diagnostic("adsr") for _ in range(3)])
        self.assertEqual(q.gate_diagnostics(exact, baseline, tiers=("lint",))[0], [])
        better = q.tally([self._diagnostic("adsr")])
        failures, notes = q.gate_diagnostics(better, baseline, tiers=("lint",))
        self.assertEqual(failures, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("improved to 1", notes[0])

    def test_unjustified_waiver_fails_even_at_the_waived_count(self):
        observed = q.tally([self._diagnostic("adsr")])
        baseline = {
            "waivers": {
                "verilator|tb/sv/a.sv|WIDTHTRUNC": {
                    "count": 1, "tier": "lint", "reason": q.UNJUSTIFIED
                }
            }
        }
        failures, _ = q.gate_diagnostics(observed, baseline, tiers=("lint",))
        self.assertEqual(len(failures), 1)
        self.assertIn("no justification", failures[0])

    def test_other_tier_waivers_are_ignored_by_a_lint_only_run(self):
        baseline = {
            "waivers": {
                "lane|adsr|TB-WARN": {
                    "count": 6, "tier": "runtime", "reason": "negative control"
                }
            }
        }
        failures, notes = q.gate_diagnostics({}, baseline, tiers=("lint",))
        self.assertEqual((failures, notes), ([], []))

    def test_update_baseline_preserves_the_tier_that_did_not_run(self):
        previous = {
            "waivers": {
                "lane|adsr|TB-WARN": {
                    "count": 6, "tier": "runtime", "reason": "negative control"
                },
                "verilator|tb/sv/old.sv|WIDTHTRUNC": {
                    "count": 9, "tier": "lint", "reason": "gone now"
                },
            }
        }
        observed = q.tally([self._diagnostic("adsr")])
        rewritten = q.build_baseline(observed, previous, tiers=("lint",))
        self.assertIn("lane|adsr|TB-WARN", rewritten["waivers"])
        self.assertNotIn(
            "verilator|tb/sv/old.sv|WIDTHTRUNC",
            rewritten["waivers"],
            "a lint waiver the lint tier no longer observes must be dropped",
        )
        self.assertIn("verilator|tb/sv/a.sv|WIDTHTRUNC", rewritten["waivers"])
        self.assertEqual(rewritten["policy"], q.POLICY_DOC)

    def test_committed_ledger_is_fully_justified(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(baseline["schema_version"], 1)
        self.assertEqual(baseline["policy"], q.POLICY_DOC)
        self.assertTrue(baseline["waivers"], "the ledger must not be empty")
        for key, waiver in baseline["waivers"].items():
            with self.subTest(key=key):
                self.assertIn(waiver["tier"], ("lint", "runtime"))
                self.assertGreater(waiver["count"], 0)
                self.assertFalse(
                    str(waiver["reason"]).startswith("UNJUSTIFIED"),
                    "every committed waiver must state why it is waived",
                )
                self.assertGreater(len(waiver["reason"]), 30)

    def test_committed_ledger_excludes_the_declared_exclusion_classes(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(baseline["excluded_classes"], dict(sorted(
            q.EXCLUDED_CLASSES.items()
        )))
        for key in baseline["waivers"]:
            code = key.rsplit("|", 1)[1]
            self.assertNotIn(
                code,
                q.EXCLUDED_CLASSES,
                "an excluded class must never reach the ledger",
            )


class CoverageTests(unittest.TestCase):
    TRANSCRIPT = (
        "TB-DONE 1\n"
        "case attack-sweep: RTL sample-exact -> OK\n"
        "case boundary-tie: RTL sample-exact -> OK\n"
        "mutation wrong-sustain-level (RTL): DETECTED (test fails the mutant)\n"
        "mutation off-by-one timing (RTL): DETECTED (test fails the mutant)\n"
    )

    def test_counts_verdicts_and_mutation_markers(self):
        observed = q.lane_coverage(self.TRANSCRIPT)
        self.assertEqual(observed["cases_ok"], 2)
        self.assertEqual(observed["cases_failed"], 0)
        self.assertEqual(observed["mutations_detected"], 2)
        self.assertEqual(observed["mutations_undetected"], 0)
        self.assertEqual(observed["tb_completions"], 1)

    def test_tb_done_alone_is_not_coverage(self):
        observed = q.lane_coverage("TB-DONE 1\nTB-DONE 1\n")
        self.assertEqual(observed["cases_ok"], 0)
        self.assertEqual(observed["mutations_detected"], 0)
        self.assertTrue(q.gate_coverage("adsr", observed))

    def test_not_detected_mutation_fails(self):
        observed = q.lane_coverage(
            self.TRANSCRIPT
            + "mutation wrong-lut-address (RTL, case source:vco_1): NOT DETECTED\n"
        )
        self.assertEqual(observed["mutations_undetected"], 1)
        failures = q.gate_coverage("adsr", observed)
        self.assertTrue(any("NOT DETECTED" in text for text in failures))

    def test_fail_verdict_fails(self):
        observed = q.lane_coverage(
            self.TRANSCRIPT + "fixture class silence: carried by x -> FAIL\n"
        )
        failures = q.gate_coverage("adsr", observed)
        self.assertTrue(any("-> FAIL" in text for text in failures))

    def test_shrinking_below_the_declared_minimum_fails(self):
        obligations = q.COVERAGE_MODEL["adsr"]
        self.assertGreaterEqual(obligations.min_mutations, 3)
        observed = q.lane_coverage(
            "case a: -> OK\nmutation one (RTL): DETECTED\n"
        )
        failures = q.gate_coverage("adsr", observed)
        self.assertTrue(any("below its declared minimum" in t for t in failures))

    def test_a_clean_lane_passes_its_obligations(self):
        rich = self.TRANSCRIPT + "mutation linear-vs-exp curve (vector side): DETECTED\n"
        rich += "".join("case c%d: -> OK\n" % i for i in range(6))
        self.assertEqual(q.gate_coverage("adsr", q.lane_coverage(rich)), [])


class RegistrationTests(unittest.TestCase):
    def test_the_registered_check_runs_this_suite(self):
        check = CHECKS[CHECK_NAME]
        self.assertIn("test_rtl_module_qualification.py", check.command)
        self.assertEqual(check.claim_class, "implementation-identity")
        self.assertEqual(check.engine, "rtl-simulation")
        self.assertEqual(check.layer, "rtl")
        self.assertEqual(check.scope, "module-sample-exact")
        self.assertEqual(check.controls, {"one-bit-mutation": "sample-mismatch"})

    def test_every_declared_covered_input_is_present_here(self):
        for relative in CHECKS[CHECK_NAME].inputs:
            with self.subTest(relative=relative):
                self.assertTrue(
                    (ROOT / relative).is_file(),
                    "the check declares %s as a covered input" % relative,
                )

    @unittest.skipUnless(GRAPH.is_file(), "the capability graph is not in this tree")
    def test_graph_node_agrees_with_the_registered_check(self):
        graph = json.loads(GRAPH.read_text(encoding="utf-8"))
        node = next(n for n in graph["nodes"] if n["id"] == q.NODE_ID)
        check = CHECKS[CHECK_NAME]
        self.assertEqual(node["check"], CHECK_NAME)
        for field in ("claim_class", "engine", "layer", "scope"):
            self.assertEqual(node[field], getattr(check, field))
        self.assertLessEqual(
            check.controls.items(), node["negative_controls"].items()
        )
        self.assertIn(q.POLICY_DOC, node["coverage"]["inputs"])
        self.assertIn("tb/sv", node["coverage"]["inputs"])

    @unittest.skipUnless(GRAPH.is_file(), "the capability graph is not in this tree")
    @unittest.skipUnless(SIM_REFERENCE.is_dir(), "sim/reference is not in this tree")
    def test_coverage_hashes_do_not_crash_on_colon_named_vector_files(self):
        # Regression: sim/reference/sine-vco-golden-v1 and
        # sim/reference/square-saw-vco-golden-v1 (issues #73/#74) name every
        # vector with a ':'-delimited scheme (e.g.
        # "freq:mid-phase:min-unmod.json"). Walking either directory through
        # coverage_hashes() used to raise CapabilityError("unsafe relative
        # path") the moment --record actually ran -- caught only by running the
        # real command, not by reading the graph. Issue #215 fixed that walk:
        # a name found on disk is now judged by containment plus the symlink
        # and escape refusals, not by the grammar reserved for declarations, so
        # either directory hashes as stored. Whether this node should cover
        # them is a separate declaration decision; it still excludes them (see
        # spec/RTL-MODULE-QUALIFICATION.md), so both facts are asserted here:
        # the committed node hashes, and so would one that added them.
        graph = load_graph(GRAPH)
        node = next(n for n in graph["nodes"] if n["id"] == q.NODE_ID)
        vector_sets = (
            "sim/reference/sine-vco-golden-v1",
            "sim/reference/square-saw-vco-golden-v1",
        )
        for excluded in vector_sets:
            self.assertNotIn(excluded, node["coverage"]["inputs"])
        hashes = coverage_hashes(node, ROOT)
        self.assertTrue(hashes)
        covering = copy.deepcopy(node)
        covering["coverage"]["inputs"] = sorted(
            set(covering["coverage"]["inputs"]) | set(vector_sets)
        )
        covered = coverage_hashes(covering, ROOT)
        for vectors in vector_sets:
            self.assertRegex(covered[vectors], r"\A[0-9a-f]{64}\Z")
            self.assertTrue(any(":" in p.name for p in (ROOT / vectors).iterdir()))

    @unittest.skipUnless(GRAPH.is_file(), "the capability graph is not in this tree")
    def test_evidence_stays_detached_while_the_prerequisite_is_unproven(self):
        graph = json.loads(GRAPH.read_text(encoding="utf-8"))
        indexed = {node["id"]: node for node in graph["nodes"]}
        node = indexed[q.NODE_ID]
        unproven = [
            name for name in node["dependencies"] if indexed[name]["evidence"] is None
        ]
        if unproven:
            self.assertIsNone(
                node["evidence"],
                "attaching evidence while %s carries none evaluates BLOCKED and "
                "unhealthy under compile_capabilities.py --strict; see %s"
                % (", ".join(unproven), q.POLICY_DOC),
            )


class RecordTests(unittest.TestCase):
    NODE = {
        "id": "rtl-modules",
        "engine": "rtl-simulation",
        "layer": "rtl",
        "scope": "module-sample-exact",
        "negative_controls": {"one-bit-mutation": "sample-mismatch"},
    }

    def _record(self, **overrides):
        arguments = dict(
            node=self.NODE,
            node_sha256="a" * 64,
            inputs={"tb/sv": "b" * 64},
            project_commit="c" * 40,
            run_id="run-0123456789abcdef",
            recorded_at="2026-09-26T12:00:00Z",
            command=CHECKS[CHECK_NAME].command,
            exit_code=0,
            log_path="out/rtl-modules.log",
            log_sha256="d" * 64,
            verdict="PASS",
            reason="lint units 12, lanes 11/11",
            control_executed=True,
            control_detected=True,
        )
        arguments.update(overrides)
        return q.build_record(**arguments)

    def test_record_validates_against_the_evidence_schema(self):
        _validate(self._record(), "evidence")

    def test_record_indexes_the_transcript_and_the_control(self):
        record = self._record()
        self.assertEqual(record["execution"]["log"]["path"], "out/rtl-modules.log")
        self.assertEqual(record["execution"]["log"]["sha256"], "d" * 64)
        control = record["controls"]["one-bit-mutation"]
        self.assertEqual(control["fault"], "sample-mismatch")
        self.assertTrue(control["executed"] and control["detected"])
        self.assertEqual(
            record["execution"]["command"], list(CHECKS[CHECK_NAME].command)
        )

    def test_unrun_check_records_no_exit_code_and_is_not_executed(self):
        record = self._record(exit_code=None, verdict="NO VERDICT", reason="skipped")
        _validate(record, "evidence")
        self.assertFalse(record["execution"]["executed"])

    def test_dependencies_are_empty_and_that_is_deliberate(self):
        record = self._record()
        self.assertEqual(
            record["dependencies"],
            {},
            "fixed-model has no evidence to cite; see " + q.POLICY_DOC,
        )
        self.assertIn("attachment precondition", POLICY.read_text(
            encoding="utf-8"
        ).lower())


class GateNegativeControlTests(unittest.TestCase):
    """The gate must be observed failing, not merely believed to work."""

    def test_planted_runtime_diagnostic_fails_the_gate(self):
        transcript = "ERROR: mixer released a sample while error was sticky\n"
        observed = q.tally(q.scan_transcript("mix", transcript))
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        failures, _ = q.gate_diagnostics(observed, baseline, tiers=("runtime",))
        self.assertTrue(
            failures, "an unwaived runtime diagnostic must fail the gate"
        )
        self.assertIn("lane|mix|SIM-ERROR", failures[0])

    @unittest.skipUnless(has_verilator(), "verilator is not installed here")
    @unittest.skipUnless(SV_TREE.is_dir(), "tb/sv is not present in this tree")
    def test_planted_width_mismatch_fails_the_gate(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(SV_TREE, root / "tb" / "sv")
            engine = root / "tb" / "sv" / "adsr_engine.sv"
            source = engine.read_text(encoding="utf-8")
            self.assertIn("module adsr_engine #(", source)
            # A deliberate, unmistakable width mismatch: a 4-bit net fed a
            # 32-bit constant. Verilator reports WIDTHTRUNC for it.
            fault = "    wire [3:0] planted_width_fault = 32'd4294967295;\n"
            head, sep, tail = source.rpartition("endmodule")
            self.assertEqual(sep, "endmodule")
            engine.write_text(head + fault + sep + tail, encoding="utf-8")

            clean, _ = q.lint_unit("adsr", ROOT)
            mutated, _ = q.lint_unit("adsr", root)
            clean_count = q.tally(clean).get(
                "verilator|tb/sv/adsr_engine.sv|WIDTHTRUNC", {"count": 0}
            )["count"]
            mutated_count = q.tally(mutated).get(
                "verilator|tb/sv/adsr_engine.sv|WIDTHTRUNC", {"count": 0}
            )["count"]
            self.assertGreater(
                mutated_count,
                clean_count,
                "the planted width fault produced no new WIDTHTRUNC; the "
                "mutation, not the gate, is what needs fixing here",
            )
            self.assertEqual(
                q.gate_diagnostics(q.tally(clean), baseline, tiers=("lint",))[0],
                [],
                "the unmutated tree must pass the committed ledger",
            )
            failures, _ = q.gate_diagnostics(
                q.tally(mutated), baseline, tiers=("lint",)
            )
            self.assertTrue(
                failures,
                "a planted width mismatch must fail the ratchet",
            )
            self.assertTrue(
                any("increased to %d" % mutated_count in text for text in failures),
                failures,
            )

    @unittest.skipUnless(has_verilator(), "verilator is not installed here")
    @unittest.skipUnless(SV_TREE.is_dir(), "tb/sv is not present in this tree")
    def test_the_committed_tree_passes_the_committed_ledger(self):
        diagnostics: list = []
        for unit in sorted(q.LINT_UNITS):
            found, _ = q.lint_unit(unit, ROOT)
            diagnostics += found
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        failures, _ = q.gate_diagnostics(
            q.tally(diagnostics), baseline, tiers=("lint",)
        )
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
