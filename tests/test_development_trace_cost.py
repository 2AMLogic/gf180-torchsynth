"""Synthetic controls for the #287 development trace-cost runner and verifier.

Fake backends, a fake admission gate and a three-case subset stand in for the
qualified runtime. Nothing here renders TorchSynth, starts Docker or touches a
holdout identity, and nothing here is runtime evidence: these tests establish
only that the accounting and verification logic behave as specified.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import io
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import development_trace_cost as dtc  # noqa: E402
from torchsynth_voice import trace_artifacts as ta  # noqa: E402
from torchsynth_voice.artifact_renderer import (  # noqa: E402
    QUALIFICATION_SHA256,
    PROFILE,
    digest,
    json_bytes,
)
from torchsynth_voice.artifacts import (  # noqa: E402
    ValidationError,
    canonical_bytes,
    loads,
)

from test_artifact_renderer import template as base_template  # noqa: E402
from test_trace_artifacts import SUBSET, TracedBackend  # noqa: E402


def load_tool():
    path = ROOT / "tools/measure_development_trace_cost.py"
    spec = importlib.util.spec_from_file_location("measure_development_trace_cost", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load_tool()

INDICES = [0, 1, 2]
CLEAN_GIT = base_template()["project_git"]


def subset_selection():
    names = ta.traced_request_fields(names=SUBSET)["requested_traces"]
    return dict(
        requested_traces=names,
        excluded_normalization_seams=list(ta.NORMALIZATION_SEAMS),
        selection_sha256=digest(canonical_bytes(names)),
    )


def subset_template():
    value = base_template()
    value.update(ta.traced_request_fields(names=SUBSET))
    return value


def subset_expected():
    return dict(
        indices=list(INDICES),
        selection=subset_selection(),
        registry=ta.registry_binding(),
        manifest=dtc.manifest_binding(),
        runtime=tool.production_expected()["runtime"],
        project_git=dict(CLEAN_GIT),
        template_sha256=digest(canonical_bytes(subset_template())),
        telemetry_driver_sha256=digest(tool.MEASURE_DRIVER_SOURCE.encode()),
    )


def raw_telemetry(*, system="Linux", rss=(500_000, 900_000, 910_000), marks=None):
    marks = marks or dict(
        driver_prelude=10.0,
        driver_imports_done=12.5,
        render_call_start=12.5,
        render_call_end=13.75,
        driver_end=14.0,
    )
    return json.dumps(
        dict(
            schema=dtc.TELEMETRY_SCHEMA,
            schema_version=1,
            clock=dtc.CLOCK,
            rss_source=dtc.RSS_SOURCE,
            system=system,
            machine="x86_64",
            marks_seconds=marks,
            ru_maxrss_kib=dict(zip(dtc.RSS_POINTS, rss)),
        )
    ).encode()


class TelemetryBackend(TracedBackend):
    """Synthetic traced backend whose receipt carries worker telemetry."""

    def __init__(self, *, skip=(), fail=()):
        super().__init__()
        self.skip = set(skip)
        self.fail = set(fail)

    def __call__(self, request, *, capture_provider=None):
        index = request["fixture"]["sound_index"]
        if index in self.fail:
            raise RuntimeError("synthetic worker failure")
        observation = super().__call__(request, capture_provider=capture_provider)
        if index not in self.skip:
            observation["receipt"]["worker_telemetry"] = dtc.normalize_telemetry(
                raw_telemetry(rss=(500_000 + index, 900_000 + index, 910_000 + index)),
                20.0 + index,
            )
        return observation


def fake_admission(publication):
    return {"synthetic_host": True}, publication["provenance"]["image"]["Id"]


def refusing_admission(publication):
    raise subprocess.CalledProcessError(255, ["sysctl", "-n", "machdep.cpu.brand_string"])


class Campaign:
    """One synthetic three-case campaign in a private temporary directory."""

    def __init__(self, backend=None, **overrides):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.store = self.root / "store"
        self.receipt_path = self.root / "receipt.json"
        self.backend = backend or TelemetryBackend()
        self.calls = 0
        self.overrides = overrides

    def run(self, **kwargs):
        options = dict(
            command=["synthetic"],
            admission=fake_admission,
            backend_factory=lambda: self.backend,
            template=subset_template(),
            indices=INDICES,
            selection=subset_selection(),
            expected=subset_expected(),
        )
        options.update(self.overrides)
        options.update(kwargs)
        with patch.object(tool, "project_identity", return_value=CLEAN_GIT), \
                contextlib.redirect_stdout(io.StringIO()):
            return tool.run_campaign(
                kwargs.pop("store", self.store),
                kwargs.pop("receipt_path", self.receipt_path),
                **{k: v for k, v in options.items()
                   if k not in ("store", "receipt_path")},
            )

    def cleanup(self):
        self.temporary.cleanup()


def production_plan():
    template = base_template()
    template.update(
        ta.traced_request_fields(names=dtc.selection_binding()["requested_traces"])
    )
    return dtc.freeze_plan(
        project_git=template["project_git"],
        template=template,
        qualification_sha256=QUALIFICATION_SHA256,
        image=tool.production_expected()["runtime"]["image"],
        profile=PROFILE,
        driver_sha256=digest(tool.MEASURE_DRIVER_SOURCE.encode()),
    )


def production_unrun():
    return dtc.unrun_receipt(
        plan=production_plan(),
        execution=dict(outcome="unrun", admitted=False, reason="synthetic refusal"),
    )


class InventoryTests(unittest.TestCase):
    """The 96-identity denominator, at document level."""

    def test_unrun_receipt_enumerates_all_96_identities(self):
        receipt = production_unrun()
        self.assertEqual(len(receipt["cases"]), 96)
        self.assertEqual(
            [r["case_id"] for r in receipt["cases"]],
            ["global-%d" % i for i in range(96)],
        )
        self.assertTrue(all(r["state"] == "unrun" for r in receipt["cases"]))
        self.assertEqual(dtc.verify_document(receipt, expected=tool.production_expected()), dtc.UNRUN)
        self.assertEqual(len(receipt["completeness"]["states"]["unrun"]), 96)

    def assert_rejected(self, receipt, message):
        with self.assertRaisesRegex(ValidationError, message):
            dtc.verify_document(receipt, expected=tool.production_expected())

    def test_duplicate_missing_unexpected_and_reordered_rows(self):
        base = production_unrun()
        duplicate = copy.deepcopy(base)
        duplicate["cases"][1] = copy.deepcopy(duplicate["cases"][0])
        self.assert_rejected(duplicate, "duplicate case row")
        missing = copy.deepcopy(base)
        del missing["cases"][40]
        self.assert_rejected(missing, "missing case rows: global-40")
        unexpected = copy.deepcopy(base)
        unexpected["cases"].append(dict(unexpected["cases"][0], case_id="global-96",
                                        sound_index=96))
        self.assert_rejected(unexpected, "unexpected case rows: global-96")
        reordered = copy.deepcopy(base)
        reordered["cases"][0], reordered["cases"][1] = (
            reordered["cases"][1], reordered["cases"][0])
        self.assert_rejected(reordered, "not in plan order")

    def test_plan_with_95_or_97_identities_or_holdout_rejected(self):
        for indices in (list(range(95)), list(range(96)) + [96]):
            receipt = production_unrun()
            receipt["plan"]["indices"] = indices
            self.assert_rejected(receipt, "frozen development set")
        with self.assertRaisesRegex(ValidationError, "holdout"):
            dtc.freeze_plan(
                project_git=CLEAN_GIT,
                template=subset_template(),
                qualification_sha256=QUALIFICATION_SHA256,
                image="x",
                profile=PROFILE,
                driver_sha256="0" * 64,
                indices=[95, 96],
                selection=subset_selection(),
            )

    def test_unrun_receipt_cannot_carry_results(self):
        receipt = production_unrun()
        receipt["cases"][3]["state"] = "completed"
        self.assert_rejected(receipt, "completed row lacks measurements")
        receipt = production_unrun()
        receipt["storage"] = {"full_corpus": {"trace_payload_bytes": 1}}
        self.assert_rejected(receipt, "unrun receipt carries measurements")
        receipt = production_unrun()
        receipt["execution"]["outcome"] = "executed"
        self.assert_rejected(receipt, "unrun receipt claims an execution")

    def test_selection_and_registry_bindings(self):
        receipt = production_unrun()
        receipt["plan"]["selection"]["requested_traces"] = list(
            reversed(receipt["plan"]["selection"]["requested_traces"]))
        self.assert_rejected(receipt, "selection")
        receipt = production_unrun()
        receipt["plan"]["registry"]["token"] = "tr1-" + "0" * 64
        self.assert_rejected(receipt, "registry identity is stale")
        receipt = production_unrun()
        receipt["plan"]["producer"]["dirty"] = True
        self.assert_rejected(receipt, "dirty")

    def test_production_selection_is_29_traces_without_seams(self):
        selection = dtc.selection_binding()
        self.assertEqual(len(selection["requested_traces"]), 29)
        self.assertEqual(selection["excluded_normalization_seams"],
                         list(ta.NORMALIZATION_SEAMS))
        self.assertEqual(selection, tool.qta.selection_record())


class TelemetryTests(unittest.TestCase):
    def test_present_telemetry_derives_intervals_from_marks(self):
        value = dtc.normalize_telemetry(raw_telemetry(), 21.0)
        self.assertEqual(value["state"], "present")
        self.assertEqual(value["rss_unit"], "KiB")
        self.assertEqual(value["peak_rss_kib"], 910_000)
        self.assertEqual(
            value["elapsed_seconds"],
            dict(
                worker_imports_seconds=2.5,
                worker_render_call_seconds=1.25,
                worker_serialization_seconds=0.25,
                worker_driver_seconds=4.0,
                launcher_container_seconds=21.0,
            ),
        )
        self.assertTrue(dtc.validate_telemetry(json.loads(json.dumps(value))))

    def test_missing_and_invalid_telemetry_stays_missing_not_zero(self):
        cases = {
            "absent": None,
            "non-linux": raw_telemetry(system="Darwin"),
            "decreasing": raw_telemetry(rss=(900, 800, 950)),
            "zero": raw_telemetry(rss=(0, 0, 0)),
            "non-monotonic": raw_telemetry(marks=dict(
                driver_prelude=5.0, driver_imports_done=4.0, render_call_start=6.0,
                render_call_end=7.0, driver_end=8.0)),
            "incomplete": json.dumps(dict(
                json.loads(raw_telemetry()), marks_seconds={"driver_prelude": 1.0}
            )).encode(),
            "garbage": b"not json",
        }
        for label, raw in cases.items():
            with self.subTest(label):
                value = dtc.normalize_telemetry(raw, 1.0)
                self.assertEqual(set(value), {"state", "reason"})
                self.assertEqual(value["state"], "missing")
                self.assertFalse(dtc.validate_telemetry(value))

    def test_rss_unit_and_interval_tampering_rejected(self):
        value = dtc.normalize_telemetry(raw_telemetry(), 21.0)
        for mutate, message in (
            (lambda v: v.update(rss_unit="bytes"), "KiB"),
            (lambda v: v["rss_kib"].update(driver_end=910_000.5), "integer KiB"),
            (lambda v: v.update(peak_rss_kib=1), "driver_end process peak"),
            (lambda v: v["elapsed_seconds"].update(worker_render_call_seconds=0.5),
             "derived worker intervals"),
            (lambda v: v["elapsed_seconds"].pop("launcher_container_seconds"),
             "launcher interval"),
        ):
            tampered = copy.deepcopy(value)
            mutate(tampered)
            with self.assertRaisesRegex(ValidationError, message):
                dtc.validate_telemetry(tampered)


class DriverTests(unittest.TestCase):
    def test_measurement_driver_embeds_unchanged_24_driver(self):
        source = tool.MEASURE_DRIVER_SOURCE
        ast.parse(source, feature_version=(3, 9))
        body = tool.qta.DRIVER_SOURCE[: -len(tool._DRIVER_ENTRY)]
        self.assertIn(body, source)
        self.assertIn("RUSAGE_SELF", source)
        self.assertTrue(source.rstrip().endswith("_telemetry_main()"))
        for mark in dtc.MARKS:
            self.assertIn('"%s"' % mark, source)
        for point in dtc.RSS_POINTS:
            self.assertIn('"%s"' % point, source)

    def test_composed_driver_writes_valid_telemetry_with_stub_worker(self):
        """Execute the composed driver text against stub worker modules.

        Exercises the telemetry prelude/entry around the unchanged #24 body
        on this host's Python; it is not the qualified image's Python 3.9 and
        not a render.
        """
        stubs = {
            "render_artifact.py": (
                "def render_selected(request, root, capture_provider=None):\n"
                "    data = bytearray(4 * 1024 * 1024)\n"
                "    return ({'receipt': {}, 'traces': {}},"
                " {'audio': bytes(data[:8])})\n"
            ),
            "trace_capture_provider.py": (
                "class ProviderFactory:\n"
                "    def __init__(self, document, names, batch):\n"
                "        self.payloads = {n: b'xx' for n in names}\n"
                "        self.descriptors = {n: {'name': n} for n in names}\n"
            ),
            "trace_registry.py": (
                "def load_registry():\n    return {}\n"
                "def registry_token():\n    return 'tok'\n"
                "def requested_names(document, names):\n    return list(names)\n"
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stubs").mkdir()
            output = root / "output"
            output.mkdir()
            for name, text in stubs.items():
                (root / "stubs" / name).write_text(text)
            (output / "request.json").write_text(json.dumps(dict(
                trace_registry_version="tok",
                requested_traces=["a.trace"],
                execution=dict(batch_size=32),
            )))
            source = tool.MEASURE_DRIVER_SOURCE.replace('"/output', '"' + str(output))
            # The driver's own sys.path inserts name /repo paths absent here.
            (root / "driver.py").write_text(
                "import sys\nsys.path.insert(0, %r)\n" % str(root / "stubs") + source
            )
            result = subprocess.run(
                [sys.executable, "-I", str(root / "driver.py")],
                capture_output=True, text=True, cwd=root,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "traces/trace-0.bin").is_file())
            value = dtc.normalize_telemetry(
                (output / "telemetry.json").read_bytes(), 1.0
            )
            if sys.platform.startswith("linux"):
                self.assertEqual(value["state"], "present", value)
                self.assertGreater(value["peak_rss_kib"], 0)
                self.assertGreaterEqual(
                    value["elapsed_seconds"]["worker_driver_seconds"],
                    value["elapsed_seconds"]["worker_render_call_seconds"],
                )
            else:
                self.assertEqual(value["state"], "missing")

    def test_base_backend_is_unchanged_for_the_smoke(self):
        backend = tool.qta.TracedDockerBackend
        self.assertIs(backend.driver_source, tool.qta.DRIVER_SOURCE)
        self.assertIsNone(backend(project_root=ROOT).collect_telemetry(ROOT, 1.0))
        measured = tool.MeasuredTracedDockerBackend(project_root=ROOT)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                measured.collect_telemetry(directory, 3.0),
                dict(state="missing", reason="worker telemetry file absent"),
            )
            (Path(directory) / "telemetry.json").write_bytes(raw_telemetry())
            self.assertEqual(
                measured.collect_telemetry(directory, 3.0)["peak_rss_kib"], 910_000
            )

    def test_check_inputs_pins(self):
        result = tool.check_inputs()
        self.assertEqual(len(result["selection"]["requested_traces"]), 29)
        self.assertEqual(result["manifest"], dtc.manifest_binding())


class CampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.campaign = Campaign()
        cls.code, cls.receipt = cls.campaign.run()
        cls.loaded = loads(cls.campaign.receipt_path.read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.campaign.cleanup()

    def test_complete_synthetic_campaign_verifies_against_store(self):
        self.assertEqual(self.code, 0)
        self.assertEqual(self.loaded["status"], "complete")
        self.assertEqual(self.loaded, json.loads(json_bytes(self.receipt)))
        self.assertEqual(
            dtc.verify_store(self.loaded, self.campaign.store,
                             expected=subset_expected()),
            dtc.VERIFIED,
        )
        self.assertEqual(
            tool.verify_receipt(self.campaign.receipt_path, self.campaign.store,
                                expected=subset_expected()),
            0,
        )

    def test_document_only_check_states_a_limited_claim(self):
        self.assertEqual(
            dtc.verify_document(self.loaded, expected=subset_expected()), dtc.LIMITED
        )
        self.assertEqual(
            tool.verify_receipt(self.campaign.receipt_path,
                                expected=subset_expected()),
            2,
        )

    def test_synthetic_receipt_cannot_satisfy_production_pins(self):
        with self.assertRaisesRegex(ValidationError, "frozen development set"):
            dtc.verify_document(self.loaded, expected=tool.production_expected())

    def test_rows_storage_and_shared_companion_accounting(self):
        rows = self.loaded["cases"]
        self.assertEqual([r["state"] for r in rows], ["completed"] * 3)
        storage = self.loaded["storage"]
        subtotal = storage["completed_cases_subtotal"]
        self.assertEqual(storage["full_corpus"], subtotal)
        self.assertEqual(subtotal["cases"], 3)
        self.assertEqual(subtotal["audio_bytes"], 3 * 176400 * 4)
        self.assertEqual(
            subtotal["trace_payload_bytes"],
            sum(r["storage"]["trace_payload_bytes"] for r in rows),
        )
        for row in rows:
            self.assertEqual(row["storage"]["trace_payload_files"], len(SUBSET))
            self.assertNotIn("companion_bytes", row["storage"])
            self.assertEqual(row["normalization_seams"]["state"], "receipt-retained")
        categories = storage["store_inventory"]["categories"]
        self.assertEqual(categories["companion"]["files"], 1)
        self.assertEqual(
            categories["companion"]["logical_bytes"],
            storage["shared"]["companion_bytes"],
        )
        self.assertEqual(
            storage["store_inventory"]["total_logical_bytes"],
            subtotal["case_logical_bytes"]
            + storage["shared"]["companion_bytes"]
            + categories["corpus_metadata"]["logical_bytes"],
        )
        self.assertIn("allocated", storage)
        self.assertIn("not a measurement", self.loaded["projections"]["label"])

    def test_memory_and_timing_distributions(self):
        memory = self.loaded["memory"]
        self.assertEqual(memory["unit"], "KiB")
        self.assertEqual(memory["peak_rss_kib"],
                         dict(count=3, minimum=910_000, median=910_001,
                              maximum=910_002))
        self.assertEqual(memory["maximum_case"], "global-2")
        self.assertFalse(any("sum" in key for key in memory))
        timing = self.loaded["timing"]
        attempts = [r["attempt_elapsed_seconds"] for r in self.loaded["cases"]]
        self.assertEqual(timing["attempt_elapsed_seconds"]["maximum"], max(attempts))
        self.assertEqual(timing["worker_render_call_seconds"]["median"], 1.25)
        self.assertEqual(timing["launcher_container_seconds"]["minimum"], 20.0)

    def test_tampered_document_totals_rejected(self):
        for mutate, message in (
            (lambda r: r["storage"]["completed_cases_subtotal"].update(
                trace_payload_bytes=1), "storage subtotals tampered"),
            (lambda r: r["cases"][0]["storage"].update(audio_bytes=1),
             "row storage totals tampered"),
            (lambda r: r["storage"]["shared"].update(companion_bytes=1),
             "companions are not one current file"),
            (lambda r: r["memory"]["peak_rss_kib"].update(maximum=1),
             "memory summary tampered"),
            (lambda r: r.update(status="partial"), "status disagrees"),
            (lambda r: r["storage"]["store_inventory"].update(total_logical_bytes=1),
             "store logical total tampered"),
        ):
            receipt = copy.deepcopy(self.loaded)
            mutate(receipt)
            with self.assertRaisesRegex(ValidationError, message):
                dtc.verify_document(receipt, expected=subset_expected())

    def test_measured_receipt_is_never_overwritten(self):
        with self.assertRaisesRegex(ValidationError, "refusing to overwrite"):
            self.campaign.run(admission=refusing_admission,
                              store=self.campaign.root / "other")



class ResumeTests(unittest.TestCase):
    def test_resume_renders_nothing_and_keeps_original_telemetry(self):
        campaign = Campaign()
        self.addCleanup(campaign.cleanup)
        _, original = campaign.run()
        before = len(campaign.backend.calls)
        code, resumed = campaign.run(
            resume=original["run"]["run_id"],
            receipt_path=campaign.root / "resumed.json",
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(campaign.backend.calls), before)
        self.assertEqual(resumed["execution"]["renders_this_invocation"], 0)
        self.assertEqual(resumed["run"]["counts"]["resume"], 3)
        self.assertEqual(
            [r["telemetry"] for r in resumed["cases"]],
            [r["telemetry"] for r in original["cases"]],
        )
        # The original receipt now names a superseded run summary.
        with self.assertRaises(ValidationError):
            dtc.verify_store(original, campaign.store, expected=subset_expected())

    def test_resume_after_failed_case_accounts_retained_companion(self):
        campaign = Campaign(backend=TelemetryBackend(fail={1}))
        self.addCleanup(campaign.cleanup)
        code, partial = campaign.run()
        self.assertEqual((code, partial["status"]), (1, "partial"))
        self.assertEqual(partial["storage"]["shared"]["superseded_companion_files"], 0)
        campaign.backend.fail.clear()
        resumed_path = campaign.root / "resumed.json"
        code, resumed = campaign.run(
            resume=partial["run"]["run_id"], receipt_path=resumed_path
        )
        self.assertEqual((code, resumed["status"]), (0, "complete"))
        shared = resumed["storage"]["shared"]
        self.assertEqual(shared["superseded_companion_files"], 1)
        self.assertEqual(
            shared["superseded_companion_bytes"],
            partial["storage"]["shared"]["companion_bytes"],
        )
        self.assertEqual(
            resumed["storage"]["store_inventory"]["categories"]["companion"],
            dict(
                files=2,
                logical_bytes=shared["companion_bytes"]
                + shared["superseded_companion_bytes"],
            ),
        )
        self.assertEqual(
            dtc.verify_store(
                loads(resumed_path.read_bytes()), campaign.store,
                expected=subset_expected(),
            ),
            dtc.VERIFIED,
        )

    def test_resume_under_a_different_plan_refused(self):
        campaign = Campaign()
        self.addCleanup(campaign.cleanup)
        _, original = campaign.run()
        with self.assertRaisesRegex(ValidationError, "different plan"):
            campaign.run(
                resume=original["run"]["run_id"],
                receipt_path=campaign.root / "resumed.json",
                indices=[0, 1],
            )


class StoreTamperTests(unittest.TestCase):
    def setUp(self):
        self.campaign = Campaign()
        self.addCleanup(self.campaign.cleanup)
        _, self.receipt = self.campaign.run()
        self.receipt = loads(self.campaign.receipt_path.read_bytes())

    def verify(self):
        return dtc.verify_store(self.receipt, self.campaign.store,
                                expected=subset_expected())

    def trace_file(self):
        artifact = self.receipt["cases"][1]["artifact"]["artifact_id"]
        return self.campaign.store / "artifacts" / artifact / "traces/trace-0.bin"

    def test_altered_trace_bytes_rejected(self):
        path = self.trace_file()
        data = bytearray(path.read_bytes())
        data[0] ^= 1
        path.chmod(0o644)
        path.write_bytes(bytes(data))
        with self.assertRaises(ValidationError):
            self.verify()

    def test_missing_file_rejected(self):
        path = self.trace_file()
        path.unlink()
        with self.assertRaises(ValidationError):
            self.verify()

    def test_absent_store_rejected(self):
        with self.assertRaisesRegex(ValidationError, "raw store is absent"):
            dtc.verify_store(self.receipt, self.campaign.root / "nowhere",
                             expected=subset_expected())

    def test_bad_artifact_digest_rejected(self):
        self.receipt["cases"][0]["artifact"]["sha256"] = "0" * 64
        with self.assertRaises(ValidationError):
            self.verify()

    def test_unexpected_store_file_rejected(self):
        (self.campaign.store / "notes.txt").write_bytes(b"x")
        with self.assertRaisesRegex(ValidationError, "unexpected store file"):
            self.verify()

    def test_extra_bundle_breaks_inventory_reconciliation(self):
        (self.campaign.store / "trace-bundles" / "extra.json").write_bytes(b"{}")
        with self.assertRaisesRegex(ValidationError, "recomputed store measurement"):
            self.verify()

    def test_tampered_run_summary_digest_rejected(self):
        self.receipt["run"]["run_summary"]["sha256"] = "0" * 64
        with self.assertRaises(ValidationError):
            self.verify()

    def test_tampered_row_rejected_by_recomputation(self):
        self.receipt["cases"][2]["attempt_elapsed_seconds"] += 1.0
        self.receipt["timing"] = dtc.summaries(self.receipt["cases"])["timing"]
        self.assertEqual(
            dtc.verify_document(self.receipt, expected=subset_expected()),
            dtc.LIMITED,
        )
        with self.assertRaisesRegex(ValidationError, "recomputed store measurement"):
            self.verify()


class PartialAndRefusalTests(unittest.TestCase):
    def test_failed_case_is_listed_and_no_full_corpus_claim(self):
        campaign = Campaign(backend=TelemetryBackend(fail={1}))
        self.addCleanup(campaign.cleanup)
        code, receipt = campaign.run()
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "partial")
        rows = {r["case_id"]: r for r in receipt["cases"]}
        self.assertEqual(rows["global-1"]["state"], "failed")
        self.assertIn("render-RuntimeError", rows["global-1"]["failure"])
        self.assertIsNone(rows["global-1"]["storage"])
        self.assertIsNone(receipt["storage"]["full_corpus"])
        self.assertEqual(receipt["storage"]["completed_cases_subtotal"]["cases"], 2)
        self.assertEqual(receipt["completeness"]["states"]["failed"], ["global-1"])
        self.assertTrue(any(limit.startswith("PARTIAL") for limit in receipt["limits"]))
        self.assertEqual(
            dtc.verify_store(loads(campaign.receipt_path.read_bytes()),
                             campaign.store, expected=subset_expected()),
            dtc.VERIFIED,
        )
        self.assertEqual(
            tool.verify_receipt(campaign.receipt_path, campaign.store,
                                expected=subset_expected()),
            1,
        )
        tampered = loads(campaign.receipt_path.read_bytes())
        tampered["storage"]["full_corpus"] = tampered["storage"][
            "completed_cases_subtotal"]
        with self.assertRaisesRegex(ValidationError, "full-corpus total claimed"):
            dtc.verify_document(tampered, expected=subset_expected())

    def test_missing_telemetry_is_partial_memory(self):
        campaign = Campaign(backend=TelemetryBackend(skip={2}))
        self.addCleanup(campaign.cleanup)
        code, receipt = campaign.run()
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "partial")
        self.assertTrue(receipt["completeness"]["storage_complete"])
        self.assertFalse(receipt["completeness"]["memory_complete"])
        self.assertIsNotNone(receipt["storage"]["full_corpus"])
        row = receipt["cases"][2]
        self.assertEqual(row["telemetry"]["state"], "missing")
        self.assertEqual(receipt["memory"]["cases_missing_telemetry"], ["global-2"])
        self.assertEqual(receipt["memory"]["peak_rss_kib"]["count"], 2)

    def test_refused_host_records_unrun_before_store_access(self):
        campaign = Campaign()
        self.addCleanup(campaign.cleanup)
        code, receipt = campaign.run(admission=refusing_admission)
        self.assertEqual(code, 2)
        self.assertFalse(campaign.store.exists())
        self.assertEqual(campaign.backend.calls, [])
        self.assertEqual(receipt["status"], "unrun")
        self.assertIn("CalledProcessError", receipt["execution"]["reason"])
        self.assertEqual(
            tool.verify_receipt(campaign.receipt_path, expected=subset_expected()), 2
        )
        self.assertEqual(
            tool.verify_receipt(campaign.receipt_path, campaign.store,
                                expected=subset_expected()), 2
        )
        # An UNRUN receipt may be replaced by a later run.
        code, receipt = campaign.run()
        self.assertEqual((code, receipt["status"]), (0, "complete"))

    def test_holdout_refused_before_admission(self):
        campaign = Campaign()
        self.addCleanup(campaign.cleanup)
        called = []

        def admission(publication):
            called.append(True)
            return fake_admission(publication)

        with self.assertRaisesRegex(ValidationError, "holdout"):
            campaign.run(admission=admission, indices=[95, 96])
        self.assertEqual(called, [])
        self.assertFalse(campaign.store.exists())

    def test_dirty_producer_refused(self):
        campaign = Campaign()
        self.addCleanup(campaign.cleanup)
        dirty = dict(CLEAN_GIT, dirty=True)
        with patch.object(tool, "project_identity", return_value=dirty):
            with self.assertRaisesRegex(ValidationError, "clean committed"):
                tool.run_campaign(campaign.store, campaign.receipt_path,
                                  admission=fake_admission)

    def test_existing_store_refused(self):
        campaign = Campaign()
        self.addCleanup(campaign.cleanup)
        campaign.store.mkdir()
        with self.assertRaisesRegex(ValidationError, "must be fresh"):
            campaign.run()


def tree_snapshot(*roots):
    """Relative path -> bytes for every file under the given roots/files."""
    snapshot = {}
    for root in roots:
        root = Path(root)
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if path.is_file():
                snapshot[str(path)] = path.read_bytes()
    return snapshot


def _set(*path_and_value):
    *path, value = path_and_value

    def mutate(receipt):
        target = receipt
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


def _both_commits(receipt):
    receipt["plan"]["producer"]["commit"] = "c" * 40
    receipt["plan"]["project_git"]["commit"] = "c" * 40


FORGED = "sha256:" + "f" * 64
PLAN_TAMPERS = (
    ("producer commit alone", _set("plan", "producer", "commit", "c" * 40),
     "producer commit disagrees"),
    ("project commit alone", _set("plan", "project_git", "commit", "c" * 40),
     "producer commit disagrees"),
    ("substituted producer", _both_commits, "not the expected producer"),
    ("producer dirty", _set("plan", "producer", "dirty", True), "dirty"),
    ("project dirty", _set("plan", "project_git", "dirty", True), "dirty"),
    ("runtime profile", _set("plan", "runtime", "profile", "other"),
     "qualified runtime"),
    ("qualification ref", _set("plan", "runtime", "qualification_ref", "x.json"),
     "qualified runtime"),
    ("qualification digest",
     _set("plan", "runtime", "qualification_sha256", "e" * 64), "qualified runtime"),
    ("plan image", _set("plan", "runtime", "image", FORGED), "qualified runtime"),
    ("request runtime",
     _set("plan", "runtime", "request_runtime", {"python": "9.9"}),
     "qualified runtime"),
    ("template digest", _set("plan", "template_sha256", "d" * 64),
     "expected corpus template"),
    ("telemetry driver", _set("plan", "telemetry", "driver_sha256", "a" * 64),
     "expected measurement driver"),
)


class ProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.campaign = Campaign()
        _, cls.receipt = cls.campaign.run()
        cls.production = production_unrun()

    @classmethod
    def tearDownClass(cls):
        cls.campaign.cleanup()

    def mutated(self, base, mutate):
        receipt = copy.deepcopy(base)
        mutate(receipt)
        return receipt

    def test_each_measured_plan_binding_is_rejected_independently(self):
        for name, mutate, message in PLAN_TAMPERS:
            receipt = self.mutated(self.receipt, mutate)
            with self.subTest(name, check="document"):
                with self.assertRaisesRegex(ValidationError, message):
                    dtc.verify_document(receipt, expected=subset_expected())
            with self.subTest(name, check="store"):
                with self.assertRaisesRegex(ValidationError, message):
                    dtc.verify_store(receipt, self.campaign.store,
                                     expected=subset_expected())

    def test_production_shaped_unrun_plan_rejects_substitutions(self):
        expected = tool.production_expected()
        self.assertEqual(dtc.verify_document(self.production, expected=expected),
                         dtc.UNRUN)
        for name, mutate, message in PLAN_TAMPERS:
            if name in ("template digest", "substituted producer"):
                continue  # production binds these at verify_store()
            receipt = self.mutated(self.production, mutate)
            with self.subTest(name):
                with self.assertRaisesRegex(ValidationError, message):
                    dtc.verify_document(receipt, expected=expected)

    def test_incomplete_or_absent_expected_pins_fail_closed(self):
        with self.assertRaisesRegex(ValidationError, "expected pins"):
            dtc.verify_document(self.production)
        partial = tool.production_expected()
        del partial["runtime"]
        with self.assertRaisesRegex(ValidationError, "expected pins"):
            dtc.verify_document(self.production, expected=partial)

    def test_reproduced_forgery_admitted_false_with_forged_image(self):
        receipt = self.mutated(self.receipt, lambda r: r["execution"].update(
            admitted=False, image=FORGED))
        for check in (
            lambda: dtc.verify_document(receipt, expected=subset_expected()),
            lambda: dtc.verify_store(receipt, self.campaign.store,
                                     expected=subset_expected()),
        ):
            with self.assertRaisesRegex(ValidationError, "admitted execution"):
                check()

    def test_execution_contradictions_are_rejected(self):
        cases = (
            ("measured, image alone", self.receipt,
             _set("execution", "image", FORGED), "disagrees with the frozen plan"),
            ("measured, admitted alone", self.receipt,
             _set("execution", "admitted", False), "admitted execution"),
            ("measured, admitted dropped", self.receipt,
             lambda r: r["execution"].pop("admitted"), "admitted execution"),
            ("measured, unrun outcome", self.receipt,
             _set("execution", "outcome", "unrun"), "executed outcome"),
            ("unrun admitted", self.production,
             _set("execution", "admitted", True), "claims admission"),
            ("unrun with image", self.production,
             _set("execution", "image", FORGED), "claims admission"),
        )
        for name, base, mutate, message in cases:
            receipt = self.mutated(base, mutate)
            expected = (subset_expected() if base is self.receipt
                        else tool.production_expected())
            with self.subTest(name, check="document"):
                with self.assertRaisesRegex(ValidationError, message):
                    dtc.verify_document(receipt, expected=expected)
            if base is self.receipt:
                with self.subTest(name, check="store"):
                    with self.assertRaisesRegex(ValidationError, message):
                        dtc.verify_store(receipt, self.campaign.store,
                                         expected=expected)

    def test_plan_image_and_execution_image_must_move_together(self):
        def both(receipt):
            receipt["plan"]["runtime"]["image"] = FORGED
            receipt["execution"]["image"] = FORGED
        receipt = self.mutated(self.receipt, both)
        with self.assertRaisesRegex(ValidationError, "qualified runtime"):
            dtc.verify_store(receipt, self.campaign.store, expected=subset_expected())

    def test_corpus_template_must_match_plan_provenance_in_the_store(self):
        # A plan whose declared request runtime differs from the retained
        # template cannot pass even with matching expected pins.
        expected = subset_expected()
        expected["runtime"] = copy.deepcopy(expected["runtime"])
        expected["runtime"]["request_runtime"] = {"python": "9.9"}
        receipt = self.mutated(self.receipt, _set(
            "plan", "runtime", "request_runtime", {"python": "9.9"}))
        with self.assertRaises(ValidationError):
            dtc.verify_store(receipt, self.campaign.store, expected=expected)


class PublicationPreflightTests(unittest.TestCase):
    def test_refused_resume_leaves_store_journal_and_receipt_untouched(self):
        campaign = Campaign(backend=TelemetryBackend(fail={1}))
        self.addCleanup(campaign.cleanup)
        code, partial = campaign.run()
        self.assertEqual((code, partial["status"]), (1, "partial"))
        campaign.backend.fail.clear()
        calls = len(campaign.backend.calls)
        before = tree_snapshot(campaign.store, campaign.receipt_path)
        with self.assertRaisesRegex(ValidationError, "refusing to overwrite"):
            campaign.run(resume=partial["run"]["run_id"])
        self.assertEqual(len(campaign.backend.calls), calls)
        self.assertEqual(tree_snapshot(campaign.store, campaign.receipt_path), before)
        kept = loads(campaign.receipt_path.read_bytes())
        self.assertEqual(kept, json.loads(json_bytes(partial)))
        self.assertEqual(
            dtc.verify_store(kept, campaign.store, expected=subset_expected()),
            dtc.VERIFIED,
        )

    def test_occupied_destination_does_not_create_a_fresh_store(self):
        campaign = Campaign()
        self.addCleanup(campaign.cleanup)
        campaign.run()
        other = campaign.root / "other-store"
        with self.assertRaisesRegex(ValidationError, "refusing to overwrite"):
            campaign.run(store=other)
        self.assertFalse(other.exists())

    def test_distinct_destination_resume_completes_and_keeps_earlier_receipt(self):
        campaign = Campaign(backend=TelemetryBackend(fail={1}))
        self.addCleanup(campaign.cleanup)
        _, partial = campaign.run()
        first_bytes = campaign.receipt_path.read_bytes()
        campaign.backend.fail.clear()
        second = campaign.root / "resumed.json"
        code, resumed = campaign.run(
            resume=partial["run"]["run_id"], receipt_path=second)
        self.assertEqual((code, resumed["status"]), (0, "complete"))
        self.assertEqual(campaign.receipt_path.read_bytes(), first_bytes)
        self.assertEqual(
            dtc.verify_store(loads(second.read_bytes()), campaign.store,
                             expected=subset_expected()),
            dtc.VERIFIED,
        )

    def test_receipt_inside_the_store_or_a_directory_is_refused(self):
        campaign = Campaign()
        self.addCleanup(campaign.cleanup)
        for path in (campaign.store / "receipt.json", campaign.root):
            with self.subTest(path=str(path)):
                with self.assertRaises(ValidationError):
                    campaign.run(receipt_path=path)
        self.assertFalse(campaign.store.exists())


class CommittedReceiptTests(unittest.TestCase):
    def test_committed_receipt_is_unrun_or_absent_never_a_pass(self):
        path = ROOT / dtc.RECEIPT_REF
        if not path.exists():
            self.assertEqual(tool.verify_receipt(path), 2)
            return
        receipt = loads(path.read_bytes())
        state = dtc.verify_document(receipt, expected=tool.production_expected())
        self.assertIn(state, (dtc.UNRUN, dtc.LIMITED))
        self.assertEqual(tool.verify_receipt(path), 2)


if __name__ == "__main__":
    unittest.main()
