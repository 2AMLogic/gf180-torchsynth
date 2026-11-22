"""Golden-vector schema, loader, and first-mismatch reporter (issue #68)."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import golden_vectors as gv  # noqa: E402
from torchsynth_voice.digest import digest_words, sha256_bytes  # noqa: E402

REGISTRY = gv.load_registry_document()
INVENTORY = gv.load_inventory_document()
PARAMETER_NAMES = {row["name"] for row in INVENTORY["parameters"]}


def make_vector(trace_values=8, parameter=None) -> dict:
    """A valid synthetic-bringup vector for the loader/reporter tests."""
    vector = {
        "schema": gv.VECTOR_SCHEMA,
        "schema_version": gv.SCHEMA_VERSION,
        "trace_registry_version": REGISTRY["semantic_version"],
        "parameter_inventory_commit": INVENTORY["source"]["commit"],
        "clip": {
            "profile": gv.SYNTHETIC_PROFILE,
            "sample_rate_hz": 44100,
            "sample_count": trace_values,
            "control_rate_hz": 441,
            "control_count": trace_values,
        },
        "parameters": {parameter or "adsr_1.alpha": 0.5},
        "traces": [
            {
                "name": "mixer.output",
                "kind": "audio",
                "encoding": "synthetic-int",
                "cycle_start": 0,
                "values": list(range(trace_values)),
            }
        ],
    }
    vector["content_hash"] = gv.compute_content_hash(vector)
    return vector


class TestCanonicalClipProfile(unittest.TestCase):
    """The canonical timing is grounded in the live registry, not invented."""

    def test_constants_match_registry_traces(self):
        by_name = {t["name"]: t for t in REGISTRY["traces"]}
        audio = by_name["mixer.output"]
        control = by_name["adsr_1.output"]
        self.assertEqual(gv.CANONICAL_SAMPLE_RATE_HZ, audio["rate_hz"])
        self.assertEqual(gv.CANONICAL_SAMPLE_COUNT, audio["sample_count"])
        self.assertEqual(gv.CANONICAL_CONTROL_RATE_HZ, control["rate_hz"])
        self.assertEqual(gv.CANONICAL_CONTROL_COUNT, control["sample_count"])
        self.assertEqual(gv.CANONICAL_SAMPLE_COUNT, 176400)
        self.assertEqual(gv.CANONICAL_SAMPLE_RATE_HZ, 44100)
        self.assertEqual(gv.CANONICAL_CONTROL_RATE_HZ, 441)
        self.assertEqual(gv.CANONICAL_CONTROL_COUNT, 1764)

    def test_canonical_profile_enforces_canonical_timing(self):
        vector = make_vector()
        vector["clip"] = {
            "profile": gv.CANONICAL_PROFILE,
            "sample_rate_hz": 44100,
            "sample_count": 8,
            "control_rate_hz": 441,
            "control_count": 8,
        }
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_unknown_profile_rejected(self):
        vector = make_vector()
        vector["clip"]["profile"] = "mystery-profile"
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)


class TestLoader(unittest.TestCase):
    def test_valid_vector_round_trips_through_a_file(self):
        vector = make_vector()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "golden.json"
            path.write_text(json.dumps(vector), encoding="utf-8")
            loaded = gv.load_vector(path)
        self.assertEqual(loaded["traces"][0]["values"], list(range(8)))

    def test_unknown_trace_name_rejected(self):
        vector = make_vector()
        vector["traces"][0]["name"] = "not.a.registry.trace"
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_unknown_parameter_name_rejected(self):
        vector = make_vector(parameter="not.an.inventory.parameter")
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_all_78_inventory_names_accepted(self):
        self.assertEqual(len(PARAMETER_NAMES), 78)
        vector = make_vector()
        vector["parameters"] = {name: 0.25 for name in sorted(PARAMETER_NAMES)}
        vector["content_hash"] = gv.compute_content_hash(vector)
        gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_trace_kind_must_match_registry(self):
        vector = make_vector()
        vector["traces"][0]["kind"] = "control"
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_scalar_trace_single_value(self):
        vector = make_vector()
        vector["traces"] = [
            {
                "name": "mixer.peak",
                "kind": "scalar",
                "encoding": "synthetic-int",
                "cycle_start": 0,
                "values": [3],
            }
        ]
        vector["content_hash"] = gv.compute_content_hash(vector)
        gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

        vector["traces"][0]["values"] = [3, 4]
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_canonical_profile_requires_full_registry_length(self):
        vector = make_vector()
        vector["clip"] = {
            "profile": gv.CANONICAL_PROFILE,
            "sample_rate_hz": gv.CANONICAL_SAMPLE_RATE_HZ,
            "sample_count": gv.CANONICAL_SAMPLE_COUNT,
            "control_rate_hz": gv.CANONICAL_CONTROL_RATE_HZ,
            "control_count": gv.CANONICAL_CONTROL_COUNT,
        }
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError) as caught:
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)
        self.assertIn("176400", str(caught.exception))


class TestVersionChecks(unittest.TestCase):
    def test_registry_version_mismatch_rejected(self):
        vector = make_vector()
        vector["trace_registry_version"] = "voice-traces-v0"
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError) as caught:
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)
        self.assertIn("version mismatch", str(caught.exception))

    def test_inventory_commit_mismatch_rejected(self):
        vector = make_vector()
        vector["parameter_inventory_commit"] = "0" * 40
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_unsupported_schema_rejected(self):
        vector = make_vector()
        vector["schema"] = "someone-else/golden-vector-v9"
        vector["content_hash"] = gv.compute_content_hash(vector)
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)


class TestContentHash(unittest.TestCase):
    def test_tampered_value_rejected(self):
        vector = make_vector()
        vector["traces"][0]["values"][2] = 999
        with self.assertRaises(gv.VectorError) as caught:
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)
        self.assertIn("content_hash mismatch", str(caught.exception))

    def test_missing_hash_rejected(self):
        vector = make_vector()
        del vector["content_hash"]
        with self.assertRaises(gv.VectorError):
            gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)

    def test_rehashed_tamper_is_consistently_loadable(self):
        vector = make_vector()
        vector["traces"][0]["values"][2] = 999
        vector["content_hash"] = gv.compute_content_hash(vector)
        gv.load_vector(vector, registry=REGISTRY, inventory=INVENTORY)


class TestFirstMismatchReporter(unittest.TestCase):
    def test_identical_streams_report_nothing(self):
        vector = make_vector(4)
        self.assertIsNone(
            gv.first_mismatch(vector, {"mixer.output": list(range(4))})
        )

    def test_first_mismatch_carries_all_five_columns(self):
        vector = make_vector(8)
        actual = list(range(8))
        actual[5] = 105
        mismatch = gv.first_mismatch(vector, {"mixer.output": actual})
        self.assertIsNotNone(mismatch)
        self.assertEqual(mismatch.cycle, 5)
        self.assertEqual(mismatch.sample, 5)
        self.assertEqual(mismatch.trace, "mixer.output")
        self.assertEqual(mismatch.expected, 5)
        self.assertEqual(mismatch.actual, 105)
        self.assertEqual(mismatch.row().split()[0], "cycle=5")
        for column in gv.MISMATCH_COLUMNS:
            self.assertIn(column, gv.format_mismatch(mismatch))

    def test_cycle_offset_comes_from_cycle_start(self):
        vector = make_vector(4)
        vector["traces"][0]["cycle_start"] = 1000
        vector["content_hash"] = gv.compute_content_hash(vector)
        actual = [0, 1, 3, 3]
        mismatch = gv.first_mismatch(vector, {"mixer.output": actual})
        self.assertEqual(mismatch.cycle, 1002)
        self.assertEqual(mismatch.sample, 2)

    def test_length_mismatch_reported_at_first_absent_index(self):
        vector = make_vector(4)
        mismatch = gv.first_mismatch(vector, {"mixer.output": [0, 1, 2]})
        self.assertEqual(mismatch.sample, 3)
        self.assertEqual(mismatch.expected, 3)
        self.assertIsNone(mismatch.actual)

    def test_missing_captured_trace_is_an_error_not_a_mismatch(self):
        vector = make_vector(4)
        with self.assertRaises(gv.VectorError):
            gv.first_mismatch(vector, {})

    def test_exact_comparison_is_not_tolerance_based(self):
        vector = make_vector(2)
        vector["traces"][0]["encoding"] = "f32le"
        vector["traces"][0]["values"] = [0.1, 0.2]
        actual = [0.1, 0.2 + 1e-12]
        mismatch = gv.first_mismatch(vector, {"mixer.output": actual})
        self.assertIsNotNone(mismatch)


class TestAcceptedContractBinding(unittest.TestCase):
    """The accepted-contract hash link (issue #68 AC-1/AC-7).

    The real anchor vector must bind the live accepted artifacts: the
    emitted constants package, the choice register, and the accepted
    DR-0008 record (via the frozen receipt bindings). Any interface
    change must fail this check instead of silently recompiling.
    """

    @classmethod
    def setUpClass(cls):
        cls.anchor = gv.load_vector(gv.SENTINEL_VECTOR_PATH)

    def rehash(self, vector):
        vector["content_hash"] = gv.compute_content_hash(vector)
        return vector

    def test_live_anchor_binds_the_live_contract(self):
        verified = gv.verify_accepted_contract(self.anchor)
        self.assertEqual(
            verified["constants_package_sha256"],
            gv.sha256_file(gv.CONSTANTS_PACKAGE_PATH),
        )
        self.assertEqual(
            verified["choices_register_sha256"],
            gv.sha256_file(gv.CHOICES_REGISTER_PATH),
        )
        self.assertEqual(
            verified["dr_0008_record_sha256"],
            gv.sha256_file(gv.DR_0008_RECORD_PATH),
        )

    def test_record_digest_binds_through_the_receipt(self):
        """The anchor provenance carries no record digest; the receipt does."""
        self.assertNotIn("dr_0008_record_sha256", self.anchor["provenance"])
        verified = gv.verify_accepted_contract(self.anchor)
        self.assertEqual(verified["dr_0008_record_source"], "receipt bindings")

    def test_tampered_provenance_digest_refuses(self):
        vector = dict(self.anchor)
        vector["provenance"] = dict(
            self.anchor["provenance"],
            dr_0008_constants_package_sha256="0" * 64,
        )
        with self.assertRaises(gv.VectorError) as caught:
            gv.verify_accepted_contract(self.rehash(vector))
        self.assertIn("regenerate", str(caught.exception))

    def test_modified_live_package_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "stale_pkg.sv"
            stale.write_bytes(
                Path(gv.CONSTANTS_PACKAGE_PATH).read_bytes() + b"\n// edited\n"
            )
            with self.assertRaises(gv.VectorError) as caught:
                gv.verify_accepted_contract(self.anchor, constants_path=stale)
            self.assertIn("contract refusal", str(caught.exception))

    def test_modified_live_register_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "stale_register.json"
            stale.write_bytes(
                Path(gv.CHOICES_REGISTER_PATH).read_bytes()
                .replace(b'"Accepted"', b'"Accepted "', 1)
            )
            with self.assertRaises(gv.VectorError):
                gv.verify_accepted_contract(self.anchor, register_path=stale)

    def test_modified_live_record_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "stale_record.md"
            stale.write_bytes(
                Path(gv.DR_0008_RECORD_PATH).read_bytes() + b"stale\n"
            )
            with self.assertRaises(gv.VectorError):
                gv.verify_accepted_contract(self.anchor, record_path=stale)

    def test_non_accepted_dr_status_refuses(self):
        vector = dict(self.anchor)
        vector["provenance"] = dict(
            self.anchor["provenance"], dr_0008_status="Proposed"
        )
        with self.assertRaises(gv.VectorError) as caught:
            gv.verify_accepted_contract(self.rehash(vector))
        self.assertIn("not Accepted", str(caught.exception))

    def test_missing_provenance_refuses(self):
        with self.assertRaises(gv.VectorError):
            gv.verify_accepted_contract({"schema": gv.VECTOR_SCHEMA})

    def test_unreadable_receipt_refuses_record_binding(self):
        vector = dict(self.anchor)
        vector["provenance"] = {
            k: v
            for k, v in self.anchor["provenance"].items()
            if k != "dr_0008_record_sha256"
        }
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-receipt.json"
            with self.assertRaises(gv.VectorError) as caught:
                gv.verify_accepted_contract(
                    self.rehash(vector), receipt_path=missing
                )
            self.assertIn("dr_0008_record_sha256", str(caught.exception))


def scalar_trace(value: int = 69) -> list:
    """The one-value scalar trace the VCO generators carry."""

    return [
        {
            "name": "keyboard.midi_f0",
            "kind": "scalar",
            "encoding": "synthetic-int",
            "cycle_start": 0,
            "values": [value],
        }
    ]


class TestSharedVectorAssembly(unittest.TestCase):
    """The envelope/provenance helpers the case generators share (#459).

    Five generators used to hand-build the same envelope and the same
    seven provenance fields. These assert the single shared copy still
    produces exactly what the committed vectors declare, so a change to
    it cannot silently apply to one generator and not the others.
    """

    def test_canonical_clip_is_the_five_key_canonical_block(self):
        clip = gv.canonical_clip()
        self.assertEqual(
            clip,
            {
                "profile": gv.CANONICAL_PROFILE,
                "sample_rate_hz": gv.CANONICAL_SAMPLE_RATE_HZ,
                "sample_count": gv.CANONICAL_SAMPLE_COUNT,
                "control_rate_hz": gv.CANONICAL_CONTROL_RATE_HZ,
                "control_count": gv.CANONICAL_CONTROL_COUNT,
            },
        )

    def test_canonical_clip_returns_an_independent_dict(self):
        first = gv.canonical_clip()
        first["sample_count"] = 1
        self.assertEqual(
            gv.canonical_clip()["sample_count"], gv.CANONICAL_SAMPLE_COUNT
        )

    def test_frozen_provenance_carries_the_shared_fields(self):
        provenance = gv.frozen_provenance(
            "tools/generate_adsr_golden.py", 70, "derivation text", "case:1"
        )
        self.assertEqual(provenance["generator"], "tools/generate_adsr_golden.py")
        self.assertEqual(provenance["issue"], 70)
        self.assertEqual(provenance["derivation"], "derivation text")
        self.assertEqual(provenance["case_id"], "case:1")
        self.assertEqual(provenance["model_identity"], gv.MODEL_IDENTITY)
        self.assertEqual(provenance["dr_0008_status"], gv.DR_0008_STATUS)
        self.assertEqual(
            provenance["dr_0008_constants_package_sha256"],
            gv.sha256_file(gv.CONSTANTS_PACKAGE_PATH),
        )
        self.assertEqual(
            provenance["choices_register_sha256"],
            gv.sha256_file(gv.CHOICES_REGISTER_PATH),
        )
        self.assertEqual(
            provenance["dr_0008_record_sha256"],
            gv.frozen_receipt_record_digest(),
        )
        self.assertNotIn("frozen_receipt_binding", provenance)

    def test_frozen_provenance_declares_the_accepted_dr_0008_status(self):
        """The status string is what verify_accepted_contract gates on."""

        self.assertTrue(gv.DR_0008_STATUS.startswith("Accepted"))

    def test_frozen_provenance_attaches_a_receipt_binding_when_given(self):
        binding = {"receipt": "r", "case_id": "c", "verified": True}
        provenance = gv.frozen_provenance(
            "tools/generate_lfo_vca_golden.py", 71, "d", "case:1", binding
        )
        self.assertEqual(provenance["frozen_receipt_binding"], binding)

    def test_build_vector_document_assembles_the_whole_envelope(self):
        provenance = gv.frozen_provenance("tools/x.py", 70, "d", "case:1")
        document = gv.build_vector_document(
            {"adsr_1.alpha": 0.5}, scalar_trace(), provenance, validate=False
        )
        self.assertEqual(document["schema"], gv.VECTOR_SCHEMA)
        self.assertEqual(document["schema_version"], gv.SCHEMA_VERSION)
        self.assertEqual(
            document["trace_registry_version"], REGISTRY["semantic_version"]
        )
        self.assertEqual(
            document["parameter_inventory_commit"], INVENTORY["source"]["commit"]
        )
        self.assertEqual(document["clip"], gv.canonical_clip())
        self.assertEqual(document["parameters"], {"adsr_1.alpha": 0.5})
        self.assertEqual(document["provenance"], provenance)
        self.assertEqual(
            document["content_hash"], gv.compute_content_hash(document)
        )

    def test_built_document_loads_and_binds_the_live_contract(self):
        provenance = gv.frozen_provenance("tools/x.py", 70, "d", "case:1")
        document = gv.build_vector_document(
            {"adsr_1.alpha": 0.5}, scalar_trace(), provenance, validate=True
        )
        gv.load_vector(document, registry=REGISTRY, inventory=INVENTORY)
        self.assertEqual(
            gv.verify_accepted_contract(document)["constants_package_sha256"],
            gv.sha256_file(gv.CONSTANTS_PACKAGE_PATH),
        )

    def test_validate_refuses_an_unknown_trace_name(self):
        traces = scalar_trace()
        traces[0]["name"] = "not.a.registered.trace"
        with self.assertRaises(gv.VectorError):
            gv.build_vector_document(
                {"adsr_1.alpha": 0.5},
                traces,
                gv.frozen_provenance("tools/x.py", 70, "d", "case:1"),
                validate=True,
            )

    def test_validate_refuses_a_non_accepted_contract_status(self):
        provenance = gv.frozen_provenance("tools/x.py", 70, "d", "case:1")
        provenance["dr_0008_status"] = "Proposed"
        with self.assertRaises(gv.VectorError):
            gv.build_vector_document(
                {"adsr_1.alpha": 0.5}, scalar_trace(), provenance, validate=True
            )

    def test_validate_false_skips_both_checks(self):
        """The VCO generators assemble unvalidated; the flag must honor that."""

        traces = scalar_trace()
        traces[0]["name"] = "not.a.registered.trace"
        document = gv.build_vector_document(
            {"adsr_1.alpha": 0.5},
            traces,
            {"dr_0008_status": "Proposed"},
            validate=False,
        )
        self.assertEqual(
            document["traces"][0]["name"], "not.a.registered.trace"
        )


#: Full-clip length: ``gv.evidence_row`` pins word 176399, and every word
#: must survive the f32le pack/unpack round trip as an exact Q10.21 value.
SYNTHETIC_VCO_WORDS = [
    (index * 7) % 1024 - 512 for index in range(gv.CANONICAL_SAMPLE_COUNT)
]


def synthetic_vco_derivation(words=None):
    """A fake lane derivation: mirror stream plus the pinned word sites."""

    return {
        "words": {
            "keyboard.midi_f0": 69,
            "vco_x.tuning": 3,
            "vco_x.mod_depth": -5,
        },
        "init_word": 123456789,
        "streams": {
            "vco": SYNTHETIC_VCO_WORDS if words is None else words
        },
        "clamps": 2,
        "counters": {
            "records": [
                {"kind": "saturation", "count": 4},
                {"kind": "wrap", "count": 9},
            ]
        },
    }


def synthetic_static_words(derivation):
    return {
        "keyboard.midi_f0": derivation["words"]["keyboard.midi_f0"],
        "init_phase_word": derivation["init_word"],
    }


class TestSharedVcoVectorAssembly(unittest.TestCase):
    """The shared VCO vector builder both VCO generators delegate to (#535).

    The two generators used to carry line-for-line copies of the vector
    assembly and the sidecar-custody verification. These pin the single
    shared copy to both lanes' behavior with a synthetic lane, so the
    real generators' committed bytes stay pinned by their own ``--check``
    gates instead of a slow model render here.
    """

    @classmethod
    def setUpClass(cls):
        cls.words = SYNTHETIC_VCO_WORDS
        cls.words_digest = digest_words(cls.words)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.sidecar = Path(cls.tmp.name) / "case.vco_x.raw.f32le"
        cls.sidecar.write_bytes(gv.pack_words_f32le(cls.words))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def build(self, frozen=None, rendered_words=...):
        return gv.build_vco_vector(
            case_id="case:synthetic",
            physical={"keyboard.midi_f0": 69.0},
            formats=None,
            trace_name="vco_x.raw",
            mirror_stream_key="vco",
            derive_case=lambda formats, physical: synthetic_vco_derivation(),
            static_words=synthetic_static_words,
            issue=499,
            generator="tools/generate_fake_vco_golden.py",
            shadow_boundary="declared shadow boundary text",
            frozen=frozen,
            rendered_words=None if rendered_words is ... else rendered_words,
        )

    def frozen_binding(self, digest=None):
        return {
            "golden_case_id": "boundary:synthetic",
            "digest": digest if digest is not None else self.words_digest,
            "sidecar_file": str(self.sidecar),
            "sidecar_sha256": sha256_bytes(self.sidecar.read_bytes()),
        }

    def test_frozen_sidecar_path_builds_the_binding_vector(self):
        vector = self.build(frozen=self.frozen_binding())
        provenance = vector["provenance"]
        self.assertEqual(provenance["vco_x_raw"], gv.evidence_row(self.words))
        self.assertEqual(provenance["frozen_binding"]["verified"], True)
        self.assertEqual(
            provenance["frozen_binding"]["case_id"], "boundary:synthetic"
        )
        self.assertEqual(
            provenance["frozen_binding"]["golden_document"],
            "sim/reference/fixed-voice-golden-v1.json",
        )
        self.assertEqual(
            provenance["frozen_binding"]["trace_digest"], self.words_digest
        )
        self.assertEqual(
            provenance["frozen_binding"]["sidecar"]["file"],
            str(self.sidecar),
        )
        self.assertNotIn("derivation", provenance)

    def test_provenance_carries_the_vco_lane_strings_verbatim(self):
        """#535 out-of-scope guard: the committed provenance strings stay
        byte-identical, NOT the control-path ``MODEL_IDENTITY`` text."""

        provenance = self.build(frozen=self.frozen_binding())["provenance"]
        self.assertEqual(provenance["model_identity"], "fixed-voice-v1")
        self.assertEqual(provenance["dr_0008_status"], "Accepted")
        self.assertNotEqual(provenance["model_identity"], gv.MODEL_IDENTITY)
        self.assertNotEqual(provenance["dr_0008_status"], gv.DR_0008_STATUS)
        self.assertEqual(
            provenance["dr_0008_constants_package_sha256"],
            gv.sha256_file(gv.CONSTANTS_PACKAGE_PATH),
        )
        self.assertEqual(
            provenance["choices_register_sha256"],
            gv.sha256_file(gv.CHOICES_REGISTER_PATH),
        )
        self.assertEqual(provenance["issue"], 499)
        self.assertEqual(
            provenance["generator"], "tools/generate_fake_vco_golden.py"
        )
        self.assertEqual(
            provenance["shadow_boundary"], "declared shadow boundary text"
        )

    def test_assembly_carries_the_lane_hooks_and_scalar_trace(self):
        vector = self.build(frozen=self.frozen_binding())
        self.assertEqual(
            vector["provenance"]["static_words"],
            {"keyboard.midi_f0": 69, "init_phase_word": 123456789},
        )
        self.assertEqual(vector["parameters"], {"keyboard.midi_f0": 69.0})
        self.assertEqual(vector["traces"], scalar_trace(69))
        self.assertEqual(
            vector["content_hash"], gv.compute_content_hash(vector)
        )

    def test_dedicated_render_path_builds_the_evidence_row(self):
        vector = self.build(rendered_words=list(self.words))
        provenance = vector["provenance"]
        self.assertEqual(
            provenance["vco_x_raw"]["words_sha256"], self.words_digest
        )
        self.assertIsNone(provenance["frozen_binding"])
        self.assertIn("vco_x.raw words", provenance["derivation"])
        self.assertIn("host mirror", provenance["derivation"])

    def test_frozen_digest_mismatch_refuses(self):
        with self.assertRaises(SystemExit) as caught:
            self.build(frozen=self.frozen_binding(digest="0" * 64))
        self.assertIn(
            "mirror does not reproduce the frozen vco_x.raw digest",
            str(caught.exception),
        )

    def test_frozen_sidecar_byte_drift_refuses(self):
        frozen = self.frozen_binding()
        frozen["sidecar_sha256"] = sha256_bytes(b"drifted")
        with self.assertRaises(SystemExit) as caught:
            self.build(frozen=frozen)
        self.assertIn("frozen sidecar bytes drifted", str(caught.exception))

    def test_dedicated_render_mismatch_refuses(self):
        drifted = list(self.words)
        drifted[176399] = drifted[176399] + 1
        with self.assertRaises(SystemExit) as caught:
            self.build(rendered_words=drifted)
        self.assertIn(
            "mirror does not reproduce the model's own vco_x.raw render",
            str(caught.exception),
        )


class TestSharedVcoVectorSet(unittest.TestCase):
    """``gv.build_vco_vectors`` iteration over the live frozen receipt."""

    def test_frozen_and_regime_iteration_forward_the_lane_inputs(self):
        receipt = json.loads(gv.RECEIPT_PATH.read_bytes())
        case = next(
            c
            for c in receipt["cases"]
            if c["id"] == "boundary:vco_1.initial_phase:upper"
        )
        calls = {}
        post_check_seen = {}

        def build_case_vector(
            case_id, physical, formats, *extras, frozen=None, rendered_words=None
        ):
            calls[case_id] = (physical, extras, frozen, rendered_words)
            return {"case": case_id}

        words = [1, 2, 3]

        def post_check(vectors):
            post_check_seen["vectors"] = vectors

        with mock.patch.object(
            gv, "render_fixed_trace", return_value=words
        ) as render:
            vectors = gv.build_vco_vectors(
                formats="FORMATS",
                directed_path=ROOT / "spec/reference/directed-voice-v1.json",
                trace_name="vco_1.raw",
                frozen_sidecar_cases=("boundary:vco_1.initial_phase:upper",),
                regime_cases=[("case:x", {"vco_1.tuning": 1.0}, "REGIME")],
                build_case_vector=build_case_vector,
                frozen_case_extras=lambda case: ("EXTRA",),
                post_check=post_check,
            )

        frozen_id = "frozen:boundary:vco_1.initial_phase:upper"
        self.assertEqual(set(vectors), {frozen_id, "case:x"})
        self.assertIs(post_check_seen["vectors"], vectors)

        physical, extras, frozen, rendered = calls[frozen_id]
        self.assertEqual(physical, case["parameters"])
        self.assertEqual(extras, ("EXTRA",))
        self.assertEqual(frozen["golden_case_id"], case["id"])
        self.assertEqual(frozen["digest"], case["traces"]["vco_1.raw"])
        self.assertEqual(
            frozen["sidecar_file"],
            "sim/reference/fixed-voice-golden-v1-traces/"
            "boundary:vco_1.initial_phase:upper.vco_1.raw.f32le",
        )
        self.assertEqual(
            frozen["sidecar_sha256"],
            gv.sha256_file(gv.ROOT / frozen["sidecar_file"]),
        )
        self.assertIsNone(rendered)

        rphysical, rextras, rfrozen, rrendered = calls["case:x"]
        self.assertEqual(rphysical["vco_1.tuning"], 1.0)
        self.assertEqual(rextras, ("REGIME",))
        self.assertIsNone(rfrozen)
        self.assertEqual(rrendered, words)
        render.assert_called_once_with("FORMATS", rphysical, "vco_1.raw")


class TestSharedVcoDriver(unittest.TestCase):
    """``gv.run_vco_generator``'s write path and ``--check`` refusals (#535).

    The orphan-file refusal is the drift this issue closes: only the sine
    lane's copy carried it, so a stale square/saw ``.json`` passed
    ``--check`` unnoticed.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = Path(self.tmp.name) / "golden"
        self.vectors = {
            "frozen:case:1": make_vector(4),
            "directed:case:2": make_vector(5),
        }
        self.recorded_verbose = None

    def build_all(self, formats, verbose):
        self.recorded_verbose = verbose
        return {case_id: dict(vector) for case_id, vector in self.vectors.items()}

    def run_driver(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = gv.run_vco_generator(
                argv,
                description="synthetic VCO driver",
                out_dir=self.out_dir,
                build_all=self.build_all,
                tool_name="synthetic VCO",
                frozen_count=1,
                regime_label="directed",
            )
        return status, out.getvalue()

    def test_write_path_writes_each_file_and_reports_the_summary(self):
        status, output = self.run_driver([])
        self.assertEqual(status, 0)
        for case_id in self.vectors:
            path = self.out_dir / (case_id + ".json")
            self.assertTrue(path.exists(), case_id)
            committed = gv.load_vector(path)
            self.assertEqual(committed, self.vectors[case_id])
        self.assertIn(
            "wrote 2 vectors (1 frozen-binding, 1 directed regime)", output
        )

    def test_check_passes_against_committed_files(self):
        self.run_driver([])
        status, output = self.run_driver(["--check"])
        self.assertEqual(status, 0)
        self.assertIn(
            "CHECK OK: 2 committed synthetic VCO vectors regenerate "
            "exactly (deterministic)",
            output,
        )

    def test_check_refuses_a_missing_committed_file(self):
        self.run_driver([])
        (self.out_dir / "directed:case:2.json").unlink()
        status, output = self.run_driver(["--check"])
        self.assertEqual(status, 1)
        self.assertIn(
            "CHECK FAIL: directed:case:2: missing committed file", output
        )

    def test_check_refuses_a_drifted_committed_file(self):
        self.run_driver([])
        path = self.out_dir / "directed:case:2.json"
        committed = json.loads(path.read_text(encoding="utf-8"))
        committed["traces"][0]["values"][0] = 999
        committed["content_hash"] = gv.compute_content_hash(committed)
        path.write_text(
            json.dumps(committed, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        status, output = self.run_driver(["--check"])
        self.assertEqual(status, 1)
        self.assertIn(
            "CHECK FAIL: directed:case:2: regeneration differs", output
        )

    def test_check_refuses_an_orphan_committed_file(self):
        self.run_driver([])
        (self.out_dir / "orphan.json").write_text("{}", encoding="utf-8")
        status, output = self.run_driver(["--check"])
        self.assertEqual(status, 1)
        self.assertIn(
            "CHECK FAIL: orphan: committed file has no generator case",
            output,
        )

    def test_verbose_flag_reaches_build_all(self):
        """--verbose is shared: both lanes' build_all take it (#535)."""

        status, _output = self.run_driver(["--verbose"])
        self.assertEqual(status, 0)
        self.assertTrue(self.recorded_verbose)

    def test_no_verbose_flag_defaults_to_quiet(self):
        status, _output = self.run_driver([])
        self.assertEqual(status, 0)
        self.assertFalse(self.recorded_verbose)


if __name__ == "__main__":
    unittest.main()
