"""CI wiring for the issue #3 cross-artifact consistency checks.

``tools/check_reference_consolidation.py`` is the consolidation gate for the
canonical CPU reference: it proves the four landed work units (#10, #11, #12,
#88) still agree with each other and with the tree. This module is what makes
``.github/workflows/ci.yml`` run it — ``ci.yml``'s only test step is
``python -m unittest discover -s tests``, so a checker with no wrapper here is
a checker that never runs.

Three kinds of test live here, and the second kind is the load-bearing one:

1. :class:`ConsolidationTests` runs the real checks against the real tree.
2. :class:`NegativeControlTests` builds deliberately-broken synthetic trees and
   requires each check to *refuse* them. A consistency check that cannot fail
   is indistinguishable from no check at all, and this repository's own rule is
   that a test which did not run must never be reported as a pass — so the
   controls prove the gate is load-bearing, not decorative. The publication-pin
   control in particular reproduces the exact defect that motivated the module:
   a republished reference file with one of its two hard-coded digest gates
   left behind.
3. :class:`DispatchEnvironmentTests` pins the other half of that defect class —
   every real-render spawn deriving its dispatch pins from the preregistered
   plan instead of restating them, so the container the worker admits is the
   container the plan declares. ``check_dispatch_profile_sites`` enforces that
   across all seven registered spawn/worker pairs; these tests cover the shared
   helper those spawns call.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import check_reference_consolidation as consolidation  # noqa: E402

from torchsynth_voice.artifact_renderer import (  # noqa: E402
    dispatch_flags,
    dispatch_unset_flags,
    release_profile_environment,
)


class ConsolidationTests(unittest.TestCase):
    def test_committed_artifacts_are_mutually_consistent(self) -> None:
        self.assertEqual(consolidation.check(), [])

    def test_every_check_is_registered_in_the_suite(self) -> None:
        registered = {function for _, function in consolidation.CHECKS}
        declared = {
            value
            for name, value in vars(consolidation).items()
            if name.startswith("check_") and callable(value)
        }
        self.assertEqual(declared - registered, set(), "unregistered check function")

    def test_canonical_plan_hash_reimplementation_matches_the_committed_value(
        self,
    ) -> None:
        # The checker reimplements probe.py's json_bytes rather than importing
        # the container's Python 3.9 module; this is the proof it reimplemented
        # it correctly, independent of check_plan_binding's own assertion.
        matrix = consolidation.read_json(consolidation.MATRIX)
        record = consolidation.read_json(consolidation.PUBLICATION)
        self.assertEqual(
            consolidation.digest(consolidation.canonical_json_bytes(matrix)),
            record["sentinel"]["plan_sha256"],
        )

    def test_both_publication_gate_sites_pin_the_live_digest(self) -> None:
        actual = consolidation.digest(
            consolidation.read_bytes(consolidation.PUBLICATION)
        )
        for relative in consolidation.PUBLICATION_PIN_SITES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(consolidation.PUBLICATION_PIN_MESSAGE, text, relative)
            self.assertIn(actual, text, relative)


class SyntheticTreeTestCase(unittest.TestCase):
    """Base class: point the checker at a throwaway tree, restore it after."""

    def setUp(self) -> None:
        self.addCleanup(setattr, consolidation, "ROOT", consolidation.ROOT)

    def tree(self, files: dict[str, bytes]) -> Path:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        for relative, payload in files.items():
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        consolidation.ROOT = directory
        return directory

    @staticmethod
    def gate_site(literal: str) -> bytes:
        return (
            'PIN = "'
            + literal
            + '"\nraise ValueError("'
            + consolidation.PUBLICATION_PIN_MESSAGE
            + '")\n'
        ).encode()

    def publication_tree(self, pins: list[str], extra: dict[str, bytes] | None = None):
        payload = b'{"status": "PASS"}\n'
        files = {consolidation.PUBLICATION: payload}
        for relative, literal in zip(consolidation.PUBLICATION_PIN_SITES, pins):
            files[relative] = self.gate_site(literal)
        files.update(extra or {})
        return consolidation.digest(payload), self.tree(files)

    def census_tree(self, record) -> Path:
        return self.tree(
            {
                consolidation.PUBLICATION: json.dumps(record).encode(),
                consolidation.MATRIX: json.dumps(
                    consolidation.read_json(consolidation.MATRIX)
                ).encode(),
            }
        )


class NegativeControlTests(SyntheticTreeTestCase):
    """Each control must make exactly the check under test refuse."""

    def test_matching_pins_are_accepted(self) -> None:
        live = consolidation.digest(b'{"status": "PASS"}\n')
        self.publication_tree([live, live])
        self.assertEqual(consolidation.check_publication_pins(), [])

    def test_stale_companion_pin_is_refused(self) -> None:
        """The DR-0009 A2 defect: one gate updated, the other left behind."""
        live = consolidation.digest(b'{"status": "PASS"}\n')
        self.publication_tree([live, "5" * 64])
        errors = consolidation.check_publication_pins()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn(consolidation.PUBLICATION_PIN_SITES[1], errors[0])
        self.assertIn("left this gate behind", errors[0])

    def test_gate_site_that_stops_gating_is_refused(self) -> None:
        live = consolidation.digest(b'{"status": "PASS"}\n')
        _, directory = self.publication_tree([live, live])
        (directory / consolidation.PUBLICATION_PIN_SITES[1]).write_text(
            "# the gate was removed\n", encoding="utf-8"
        )
        errors = consolidation.check_publication_pins()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("no longer raises", errors[0])

    def test_unregistered_gate_site_is_refused(self) -> None:
        live = consolidation.digest(b'{"status": "PASS"}\n')
        self.publication_tree(
            [live, live], extra={"tools/rogue_gate.py": self.gate_site(live)}
        )
        errors = consolidation.check_publication_pins()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("not in PUBLICATION_PIN_SITES", errors[0])

    def dispatch_tree(
        self, spawn: bytes, worker: bytes, extra: dict[str, bytes] | None = None
    ) -> Path:
        """One registered spawn/worker pair, with the real plan's profile."""
        matrix = {
            "profile_environment": {
                "release": {"MKL_CBWR": "COMPATIBLE", "ATEN_CPU_CAPABILITY": None}
            }
        }
        spawn_path, worker_path = consolidation.DISPATCH_SPAWN_SITES[1]
        files = {
            consolidation.MATRIX: json.dumps(matrix).encode(),
            spawn_path: spawn,
            worker_path: worker,
        }
        files.update(extra or {})
        directory = self.tree(files)
        self.addCleanup(
            setattr,
            consolidation,
            "DISPATCH_SPAWN_SITES",
            consolidation.DISPATCH_SPAWN_SITES,
        )
        consolidation.DISPATCH_SPAWN_SITES = (
            consolidation.DISPATCH_SPAWN_SITES[1],
        )
        return directory

    def test_a_plan_derived_spawn_is_accepted(self) -> None:
        self.dispatch_tree(
            b"render_artifact\n*dispatch_flags(profile)\n",
            b'plan["profile_environment"]["release"]\n',
        )
        self.assertEqual(consolidation.check_dispatch_profile_sites(), [])

    def test_a_spawn_that_restates_a_declared_pin_is_refused(self) -> None:
        """The DR-0009 A2 defect's other half: a literal instead of the plan."""
        self.dispatch_tree(
            b'render_artifact\n"--env", "MKL_CBWR=COMPATIBLE",\n',
            b'plan["profile_environment"]["release"]\n',
        )
        errors = consolidation.check_dispatch_profile_sites()
        self.assertTrue(
            any("does not derive its dispatch environment" in m for m in errors), errors
        )
        self.assertTrue(any("hard-codes the dispatch pin" in m for m in errors), errors)

    def test_a_worker_that_stops_asserting_the_profile_is_refused(self) -> None:
        self.dispatch_tree(
            b"render_artifact\n*dispatch_flags(profile)\n",
            b"# the environment assertion was removed\n",
        )
        errors = consolidation.check_dispatch_profile_sites()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("no longer asserts the plan's profile_environment", errors[0])

    def test_a_stale_spawn_worker_pairing_is_refused(self) -> None:
        self.dispatch_tree(
            b"*dispatch_flags(profile)\n",
            b'plan["profile_environment"]["release"]\n',
        )
        errors = consolidation.check_dispatch_profile_sites()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("never names it", errors[0])

    def test_an_unregistered_module_that_hard_codes_a_pin_is_refused(self) -> None:
        self.dispatch_tree(
            b"render_artifact\n*dispatch_flags(profile)\n",
            b'plan["profile_environment"]["release"]\n',
            extra={"tools/rogue_spawn.py": b'"--env", "MKL_CBWR=COMPATIBLE",\n'},
        )
        errors = consolidation.check_dispatch_profile_sites()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("tools/rogue_spawn.py", errors[0])

    def gated_tree(
        self, tool: bytes, producers: dict[str, str] | None, record: str
    ) -> None:
        """The recapture-gated spawn, its producer record, and this document."""
        spawn = next(iter(consolidation.DISPATCH_RECAPTURE_GATED))
        pinning = consolidation.DISPATCH_RECAPTURE_GATED[spawn]
        if producers is None:
            producers = {spawn: consolidation.digest(tool)}
        self.tree(
            {
                spawn: tool,
                pinning: json.dumps({"producer_sha256": producers}).encode(),
                consolidation.CONSOLIDATION_RECORD: record.encode(),
            }
        )

    def test_an_intact_recapture_gated_exemption_is_accepted(self) -> None:
        spawn = next(iter(consolidation.DISPATCH_RECAPTURE_GATED))
        self.gated_tree(b'"MKL_CBWR=COMPATIBLE"\n', None, f"divergence 4: {spawn}\n")
        self.assertEqual(
            consolidation.recapture_gated_spawn_errors(spawn, f"…{spawn}…"), []
        )

    def test_an_edited_recapture_gated_spawn_is_refused(self) -> None:
        """Editing it breaks the producer binding — that is the whole reason."""
        spawn = next(iter(consolidation.DISPATCH_RECAPTURE_GATED))
        self.gated_tree(b"edited\n", {spawn: "0" * 64}, f"divergence 4: {spawn}\n")
        errors = consolidation.recapture_gated_spawn_errors(spawn, f"…{spawn}…")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("no longer matches the producer digest", errors[0])

    def test_an_unrecorded_recapture_gated_exemption_is_refused(self) -> None:
        spawn = next(iter(consolidation.DISPATCH_RECAPTURE_GATED))
        self.gated_tree(b'"MKL_CBWR=COMPATIBLE"\n', None, "no mention here\n")
        errors = consolidation.recapture_gated_spawn_errors(spawn, "no mention here")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("a known gap that is not recorded", errors[0])

    def test_an_exemption_whose_producer_pin_vanished_is_refused(self) -> None:
        spawn = next(iter(consolidation.DISPATCH_RECAPTURE_GATED))
        self.gated_tree(b'"MKL_CBWR=COMPATIBLE"\n', {}, f"divergence 4: {spawn}\n")
        errors = consolidation.recapture_gated_spawn_errors(spawn, f"…{spawn}…")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("remove the exemption", errors[0])

    def test_a_test_module_may_assert_the_pin_reaches_the_container(self) -> None:
        # env/release-era/test_qualify_repeatability.py does exactly this.
        self.dispatch_tree(
            b"render_artifact\n*dispatch_flags(profile)\n",
            b'plan["profile_environment"]["release"]\n',
            extra={"tools/test_spawn.py": b'assert "MKL_CBWR=COMPATIBLE" in command\n'},
        )
        self.assertEqual(consolidation.check_dispatch_profile_sites(), [])

    def source_gate_tree(self, body: str) -> None:
        relative, call = consolidation.SOURCE_GATED_WORKERS[0]
        self.tree({relative: body.encode()})
        self.addCleanup(
            setattr,
            consolidation,
            "SOURCE_GATED_WORKERS",
            consolidation.SOURCE_GATED_WORKERS,
        )
        consolidation.SOURCE_GATED_WORKERS = ((relative, call),)

    def test_a_gate_called_before_the_import_is_accepted(self) -> None:
        self.source_gate_tree(
            "def validate_source(root):\n    pass\n\n\n"
            "def run():\n    validate_source(root)\n    import torch\n"
        )
        self.assertEqual(consolidation.check_source_gate_precedes_import(), [])

    def test_a_gate_defined_above_but_called_below_the_import_is_refused(self) -> None:
        """The definition's position proves nothing; the call site does."""
        self.source_gate_tree(
            "def validate_source(root):\n    pass\n\n\n"
            "def run():\n    import torch\n    validate_source(root)\n"
        )
        errors = consolidation.check_source_gate_precedes_import()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("source validation must precede", errors[0])

    def test_a_removed_gate_is_refused(self) -> None:
        self.source_gate_tree("def run():\n    import torch\n")
        errors = consolidation.check_source_gate_precedes_import()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("no longer calls", errors[0])

    def test_detached_sentinel_expectation_is_refused(self) -> None:
        self.tree(
            {
                consolidation.MATRIX: json.dumps({"batch_sizes": [32]}).encode(),
                consolidation.PUBLICATION: json.dumps(
                    {"sentinel": {"plan_sha256": "0" * 64}}
                ).encode(),
            }
        )
        errors = consolidation.check_plan_binding()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("without re-registering the sentinel expectation", errors[0])

    def test_dropped_cross_runtime_disagreement_is_refused(self) -> None:
        """AC6: a disagreement may never be deleted to make the record tidy."""
        record = consolidation.read_json(consolidation.PUBLICATION)
        record["comparisons"] = [
            comparison
            for comparison in record["comparisons"]
            if comparison["kind"] != "cross_runtime"
        ]
        self.census_tree(record)
        errors = consolidation.check_repeatability_census()
        self.assertTrue(
            any(
                "no cross-runtime comparison is retained" in message
                for message in errors
            ),
            errors,
        )

    def test_a_refused_cell_cannot_pass_as_evidence(self) -> None:
        record = consolidation.read_json(consolidation.PUBLICATION)
        record["cells"][7]["status"] = "NO_VERDICT"
        self.census_tree(record)
        errors = consolidation.check_repeatability_census()
        self.assertTrue(any("NO_VERDICT" in message for message in errors), errors)

    def test_a_failed_repeat_comparison_cannot_pass_as_evidence(self) -> None:
        record = consolidation.read_json(consolidation.PUBLICATION)
        for comparison in record["comparisons"]:
            if comparison["kind"] == "repeat":
                comparison["status"] = "FAIL"
                break
        self.census_tree(record)
        errors = consolidation.check_repeatability_census()
        self.assertTrue(
            any("repeat comparisons with status PASS" in message for message in errors),
            errors,
        )


class DispatchEnvironmentTests(unittest.TestCase):
    """The shared spawn helper describes exactly the plan's declared profile."""

    def test_profile_is_read_from_the_preregistered_plan(self) -> None:
        plan = consolidation.read_json(consolidation.MATRIX)
        self.assertEqual(
            release_profile_environment(), plan["profile_environment"]["release"]
        )

    def test_every_declared_pin_reaches_the_container(self) -> None:
        profile = release_profile_environment()
        flags = dispatch_flags(profile)
        unset = dispatch_unset_flags(profile)
        for key, value in profile.items():
            if value is None:
                self.assertIn(key, unset, key)
                self.assertNotIn(key, "".join(flags), key)
            else:
                self.assertIn(key + "=" + value, flags, key)
        self.assertEqual(len(flags), 2 * sum(1 for v in profile.values() if v is not None))
        self.assertEqual(len(unset), 2 * sum(1 for v in profile.values() if v is None))

    def test_an_added_plan_pin_is_forwarded_without_a_code_change(self) -> None:
        # The regression this guards: A2 added two ISA pins to the plan and a
        # hard-coded spawn list kept describing the pre-amendment environment.
        profile = {"MKL_CBWR": "COMPATIBLE", "ATEN_CPU_CAPABILITY": None, "NEW_PIN": "X"}
        self.assertEqual(
            dispatch_flags(profile),
            ["--env", "MKL_CBWR=COMPATIBLE", "--env", "NEW_PIN=X"],
        )
        self.assertEqual(dispatch_unset_flags(profile), ["-u", "ATEN_CPU_CAPABILITY"])


if __name__ == "__main__":
    unittest.main()
