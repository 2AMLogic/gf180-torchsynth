"""Composed whole-voice float conformance tests: issue #43.

Exercises the composed independent float model
(``torchsynth_voice.float_voice``) end to end against the committed pinned
evidence: structural identity and coverage over all 32 checkpoints, the
exact classes (noise bytes, keyboard scalars, peak/gain digests, directed
normalization targets), numeric rows against the committed fixture buffers
under the landed limits, the composed wiring-failure mutations, and the
committed conformance record's static bindings. Stdlib only; the suite
imports no TorchSynth, Torch or NumPy and runs in CI without them.
"""

import hashlib
import json
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_interfaces as fi  # noqa: E402
from torchsynth_voice import float_voice as fv  # noqa: E402
from torchsynth_voice import trace_registry as tr  # noqa: E402
from torchsynth_voice.control_path import ControlPathModel  # noqa: E402
from torchsynth_voice.float_sources import NoiseSource  # noqa: E402

RECORD = ROOT / "sim/reference/float-voice-v1.json"
TOOL = ROOT / "tools/compare_float_voice.py"
FIXTURE_ROOT = ROOT / "tests/fixtures/float-sources"
NOISE_STREAMS = FIXTURE_ROOT / "noise-streams.json"
PITCH_GATE = "boundary:vco_1.mod_depth:upper"
SAW_GATE = "waveform:vco_2:saw"


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_of(values):
    return hashlib.sha256(fv.f32le_bytes(values)).hexdigest()


def fixture_request(case_id):
    params = json.loads((FIXTURE_ROOT / case_id / "params.json").read_bytes())
    samples = NoiseSource.resolve(params["sound_index"])
    request = fi.ResolvedRequest(
        params["sound_index"],
        params["normalized_by_name"],
        {
            "seed": 13,
            "slot": params["noise_slot"],
            "sample_count": 176400,
            "sha256": params["noise_sha256"],
            "samples": samples,
        },
        physical=params["physical_by_name"],
        execution_status="canonical-batched",
    )
    references = {}
    for name in (
        "control_upsample.vco_1_pitch",
        "control_upsample.vco_2_pitch",
        "vco_1.raw",
        "vco_2.raw",
    ):
        data = (FIXTURE_ROOT / case_id / (name + ".f32le")).read_bytes()
        references[name] = list(fv.f32le_values(data))
    return params, request, references


def directed_request(case_id):
    directed = json.loads((ROOT / "spec/reference/directed-voice-v1.json").read_bytes())
    case = [item for item in directed["cases"] if item["id"] == case_id][0]
    normalized = {name: entry["normalized"] for name, entry in directed["base"].items()}
    physical = {name: entry["physical"] for name, entry in directed["base"].items()}
    for name, override in case["overrides"].items():
        normalized[name] = override["normalized"]
        physical[name] = override["physical"]
    noise_streams = json.loads(NOISE_STREAMS.read_bytes())
    samples = NoiseSource.resolve(0)
    request = fi.ResolvedRequest(
        0,
        normalized,
        {
            "seed": 13,
            "slot": 0,
            "sample_count": 176400,
            "sha256": noise_streams["slots"]["0"]["sha256"],
            "samples": samples,
        },
        physical=physical,
        execution_status="canonical-batched",
    )
    return case, request


RENDER_CACHE = {}


def ensure_renders():
    """Shared bounded full renders, paid once regardless of class order."""

    if RENDER_CACHE:
        return
    _, pitch_request, pitch_references = fixture_request(PITCH_GATE)
    RENDER_CACHE["pitch_request"] = pitch_request
    RENDER_CACHE["pitch_references"] = pitch_references
    RENDER_CACHE["pitch"] = fv.FloatVoiceModel(pitch_request).render()
    RENDER_CACHE["pitch_repeat"] = fv.FloatVoiceModel(pitch_request).render()
    _, saw_request, saw_references = fixture_request(SAW_GATE)
    RENDER_CACHE["saw_request"] = saw_request
    RENDER_CACHE["saw_references"] = saw_references
    RENDER_CACHE["saw"] = fv.FloatVoiceModel(saw_request).render()
    _, above_request = directed_request("normalization:above")
    RENDER_CACHE["above_request"] = above_request
    RENDER_CACHE["above"] = fv.FloatVoiceModel(above_request).render()
    RENDER_CACHE["above_mutated"] = fv.FloatVoiceModel(
        above_request, {fv.MUTATION_NORMALIZE_BEFORE_MIX}
    ).render()
    _, tie_request = directed_request("normalization:tie")
    RENDER_CACHE["tie"] = fv.FloatVoiceModel(tie_request).render()
    RENDER_CACHE["pitch_swapped"] = fv.FloatVoiceModel(
        pitch_request, {fv.MUTATION_SWAPPED_VCO_PITCH}
    ).render()
    RENDER_CACHE["pitch_lfo_swap"] = fv.FloatVoiceModel(
        pitch_request, {fv.MUTATION_LFO_ADSR_SWAP}
    ).render()


FloatVoiceError = fv.FloatVoiceError


class ComposedStructure(unittest.TestCase):
    """Composition coverage and structural identity over all checkpoints."""

    @classmethod
    def setUpClass(cls):
        ensure_renders()

    def test_render_is_deterministic_per_request(self):
        rendered, _ = RENDER_CACHE["pitch"]
        repeat, _ = RENDER_CACHE["pitch_repeat"]
        self.assertEqual(rendered, repeat)

    def test_composition_covers_all_checkpoints_in_registry_order(self):
        rendered, _ = RENDER_CACHE["pitch"]
        self.assertEqual(list(rendered), fv.voice_checkpoints())
        self.assertEqual(len(fv.voice_checkpoints()), 32)

    def test_declared_outputs_match_the_checkpoint_map(self):
        document = fi.load_checkpoints()
        declared = []
        for entry in document["checkpoints"]:
            declared.extend(entry["outputs"])
        self.assertEqual(fv.voice_checkpoints(), declared)

    def test_structural_attributes_match_the_registry(self):
        attributes = {entry["name"]: entry for entry in tr.load_registry()["traces"]}
        rendered, _ = RENDER_CACHE["pitch"]
        for name, values in rendered.items():
            entry = attributes[name]
            expected = entry["sample_count"] if entry["sample_count"] else 1
            self.assertEqual(len(values), expected, name)
            self.assertEqual(
                list(fv.f32le_values(fv.f32le_bytes(values))), list(values), name
            )

    def test_upsample_endpoints_are_exact_copies_of_the_control_columns(self):
        rendered, _ = RENDER_CACHE["saw"]
        for route in tr.ROUTES:
            control = rendered["mod_matrix." + route]
            upsampled = rendered["control_upsample." + route]
            self.assertEqual(upsampled[0], control[0], route)
            self.assertEqual(upsampled[-1], control[-1], route)

    def test_noise_raw_is_the_resolved_input_bytes(self):
        rendered, _ = RENDER_CACHE["pitch"]
        self.assertEqual(
            fv.f32le_bytes(rendered["noise.raw"]),
            RENDER_CACHE["pitch_request"].noise["samples"],
        )

    def test_keyboard_scalars_are_the_consumed_physical_values(self):
        rendered, _ = RENDER_CACHE["pitch"]
        physical = RENDER_CACHE["pitch_request"].physical
        self.assertEqual(
            rendered["keyboard.midi_f0"], [fv.fm.f32(physical["keyboard.midi_f0"])]
        )
        self.assertEqual(
            rendered["keyboard.duration"], [fv.fm.f32(physical["keyboard.duration"])]
        )

    def test_unmutated_composition_reproduces_the_landed_control_path(self):
        rendered, _ = RENDER_CACHE["pitch"]
        control = ControlPathModel(RENDER_CACHE["pitch_request"]).render()
        for name, values in control.items():
            self.assertEqual(rendered[name], values, name)


class PinnedNumericRows(unittest.TestCase):
    """The committed pinned seams, compared under the landed limits."""

    @classmethod
    def setUpClass(cls):
        ensure_renders()
        cls.rubric = json.loads(
            (ROOT / "spec/reference/control-path-rubric-v1.json").read_bytes()
        )
        cls.sources = json.loads(
            (ROOT / "sim/reference/float-sources-v1.json").read_bytes()
        )
        cls.capture = json.loads(
            (ROOT / "sim/reference/trace-capture.json").read_bytes()
        )

    def rubric_tolerance(self, name):
        return self.rubric["traces"][name]["limits"]["max_abs_error"]["tolerance"]

    def max_abs_error(self, reference, candidate):
        return max(abs(a - b) for a, b in zip(reference, candidate))

    def test_composed_upsample_columns_match_the_pinned_buffers(self):
        rendered, _ = RENDER_CACHE["pitch"]
        for name in ("control_upsample.vco_1_pitch", "control_upsample.vco_2_pitch"):
            error = self.max_abs_error(RENDER_CACHE["pitch_references"][name], rendered[name])
            self.assertLessEqual(error, self.rubric_tolerance(name), name)

    def test_composed_vco_traces_match_the_pinned_buffers(self):
        rendered, _ = RENDER_CACHE["pitch"]
        limit = self.sources["limits"][PITCH_GATE]
        self.assertLessEqual(
            self.max_abs_error(
                RENDER_CACHE["pitch_references"]["vco_1.raw"], rendered["vco_1.raw"]
            ),
            limit,
        )
        self.assertLessEqual(
            self.max_abs_error(
                RENDER_CACHE["pitch_references"]["vco_2.raw"], rendered["vco_2.raw"]
            ),
            limit,
        )

    def test_saw_case_vco_2_matches_the_pinned_buffer(self):
        rendered, _ = RENDER_CACHE["saw"]
        limit = self.sources["limits"][SAW_GATE]
        self.assertLessEqual(
            self.max_abs_error(RENDER_CACHE["saw_references"]["vco_2.raw"], rendered["vco_2.raw"]),
            limit,
        )

    def test_composed_noise_digest_is_the_committed_slot_digest(self):
        rendered, _ = RENDER_CACHE["pitch"]
        noise_streams = json.loads(NOISE_STREAMS.read_bytes())
        self.assertEqual(
            digest_of(rendered["noise.raw"]), noise_streams["slots"]["0"]["sha256"]
        )

    def capture_case(self, case_id):
        return [
            item for item in self.capture["cases"] if item["case"]["id"] == case_id
        ][0]

    def test_above_case_composed_peak_gain_and_branch_are_the_captured_bytes(self):
        case = self.capture_case("normalization:above")
        branch = case["normalization_branch"]
        rendered, diagnostics = RENDER_CACHE["above"]
        self.assertEqual(digest_of(rendered["mixer.peak"]), branch["peak_sha256"])
        self.assertEqual(diagnostics["normalized_branch"], True)
        self.assertEqual(
            digest_of(rendered["mixer.gain"]),
            {entry["name"]: entry for entry in case["full_inventory"]}["mixer.gain"]["sha256"],
        )
        self.assertNotEqual(
            rendered["mixer.output"], rendered["mixer.pre_normalization"]
        )

    def test_tie_case_composed_peak_bypasses_and_preserves_bytes(self):
        case = self.capture_case("normalization:tie")
        branch = case["normalization_branch"]
        rendered, diagnostics = RENDER_CACHE["tie"]
        self.assertEqual(digest_of(rendered["mixer.peak"]), branch["peak_sha256"])
        self.assertEqual(diagnostics["normalized_branch"], False)
        self.assertEqual(
            rendered["mixer.output"], rendered["mixer.pre_normalization"]
        )

    def test_composed_peaks_equal_the_exact_directed_targets(self):
        directed = json.loads(
            (ROOT / "spec/reference/directed-voice-v1.json").read_bytes()
        )
        for case_id, render in (
            ("normalization:above", RENDER_CACHE["above"]),
            ("normalization:tie", RENDER_CACHE["tie"]),
        ):
            case = [item for item in directed["cases"] if item["id"] == case_id][0]
            self.assertEqual(
                render[0]["mixer.peak"][0],
                case["normalization_target"]["target_peak"],
                case_id,
            )


class ComposedMutationControls(unittest.TestCase):
    """Cross-module wiring faults must fail and localize."""

    @classmethod
    def setUpClass(cls):
        ensure_renders()

    def test_swapped_vco_pitch_fails_the_source_rows_and_localizes(self):
        mutated, _ = RENDER_CACHE["pitch_swapped"]
        baseline, _ = RENDER_CACHE["pitch"]
        self.assertNotEqual(mutated["vco_1.raw"], baseline["vco_1.raw"])
        # vco_2's own mod_depth is zero on this fixture, so its pitch input
        # is a dead path and its raw trace stays put: the fault still fails
        # the composed match and localizes to vco_1's pitch wiring.
        for name in fv.MUTATION_INTACT_TRACES[fv.MUTATION_SWAPPED_VCO_PITCH]:
            self.assertEqual(mutated[name], baseline[name], name)
        localization = fv.diverged_boundary(baseline, mutated)
        self.assertEqual(localization[0], "vco_1.raw")
        self.assertEqual(localization[1], "vco_1")

    def test_swapped_vco_pitch_exceeds_the_declared_limit(self):
        mutated, _ = RENDER_CACHE["pitch_swapped"]
        reference = RENDER_CACHE["pitch_references"]["vco_1.raw"]
        error = max(abs(a - b) for a, b in zip(reference, mutated["vco_1.raw"]))
        rubric = json.loads(
            (ROOT / "sim/reference/float-sources-v1.json").read_bytes()
        )
        self.assertGreater(error, rubric["limits"][PITCH_GATE])

    def test_lfo_adsr_swap_fails_the_matrix_rows_and_localizes(self):
        mutated, _ = RENDER_CACHE["pitch_lfo_swap"]
        baseline, _ = RENDER_CACHE["pitch"]
        self.assertNotEqual(
            mutated["mod_matrix.vco_1_pitch"],
            baseline["mod_matrix.vco_1_pitch"],
        )
        for name in fv.MUTATION_INTACT_TRACES[fv.MUTATION_LFO_ADSR_SWAP]:
            self.assertEqual(mutated[name], baseline[name], name)
        localization = fv.diverged_boundary(baseline, mutated)
        self.assertEqual(localization[0], "mod_matrix.vco_1_pitch")
        self.assertEqual(localization[1], "mod_matrix")

    def test_normalize_before_mix_fails_the_mixer_rows_and_localizes(self):
        baseline, _ = RENDER_CACHE["above"]
        mutated, _ = RENDER_CACHE["above_mutated"]
        for name in ("mixer.pre_normalization", "mixer.peak", "mixer.gain", "mixer.output"):
            self.assertNotEqual(mutated[name], baseline[name], name)
        for name in fv.MUTATION_INTACT_TRACES[fv.MUTATION_NORMALIZE_BEFORE_MIX]:
            self.assertEqual(mutated[name], baseline[name], name)
        localization = fv.diverged_boundary(baseline, mutated)
        self.assertEqual(localization[0], "vco_1.post_vca")

    def test_normalize_before_mix_loses_the_captured_division(self):
        capture = json.loads((ROOT / "sim/reference/trace-capture.json").read_bytes())
        case = [
            item for item in capture["cases"] if item["case"]["id"] == "normalization:above"
        ][0]
        mutated, mutated_diagnostics = RENDER_CACHE["above_mutated"]
        self.assertNotEqual(
            digest_of(mutated["mixer.peak"]),
            case["normalization_branch"]["peak_sha256"],
        )
        self.assertEqual(mutated_diagnostics["normalized_branch"], False)
        self.assertEqual(mutated["mixer.output"], mutated["mixer.pre_normalization"])

    def test_unknown_mutation_is_refused(self):
        with self.assertRaises(FloatVoiceError):
            fv.FloatVoiceModel(RENDER_CACHE["pitch_request"], {"no-such-fault"})


class CommittedRecord(unittest.TestCase):
    """The committed conformance record stays bound to the tree."""

    def test_record_passes_the_static_check(self):
        completed = subprocess.run(
            [sys.executable, str(TOOL), "--check", str(RECORD)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(
            completed.returncode, 0, completed.stdout + completed.stderr
        )

    def test_record_keeps_the_classes_separate(self):
        record = json.loads(RECORD.read_bytes())
        self.assertEqual(record["numeric_contract"], "unbound:#53")
        self.assertEqual(
            set(record["class_separation"]),
            {"structural-identity", "validity", "coverage", "numeric-error"},
        )
        verdicts = set()
        for case in record["cases"]:
            for item in case.get("rows", []) + case.get("store_gated_rows", []):
                verdicts.add(item["verdict"])
        self.assertIn("STORE-GATED", verdicts)
        self.assertNotIn(None, verdicts)

    def test_record_links_raw_artifacts(self):
        record = json.loads(RECORD.read_bytes())
        linked = 0
        for case in record["cases"]:
            for name, link in case.get("raw_artifact_links", {}).items():
                linked += 1
                if "path" in link:
                    path = ROOT / link["path"]
                    self.assertTrue(path.is_file(), link["path"])
                    self.assertEqual(sha256_of(path), link["sha256"], link["path"])
        self.assertGreaterEqual(linked, 20)


class Purity(unittest.TestCase):
    def test_module_imports_no_torchsynth_torch_or_numpy(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, %r); "
                "import torchsynth_voice.float_voice; "
                "banned = ('torchsynth', 'torch', 'numpy'); "
                "loaded = [m for m in sys.modules if m.split('.')[0] in banned]; "
                "sys.exit(1 if loaded else 0)" % str(ROOT / "src"),
            ],
            capture_output=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, "a banned module was imported")


if __name__ == "__main__":
    unittest.main()
