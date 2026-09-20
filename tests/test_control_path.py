"""Independent float control-path tests (issue #40), stdlib only, no Torch.

Layers, per spec/CONTROL-PATH.md and the declared checkpoint classes:

- contract tests: owned checkpoint coverage, boundary-class discipline,
  ``unbound:#53`` numeric contract, physical measured-seam consumption,
  and the no-TorchSynth import rule;
- analytic semantics: isolated-module fixtures for every owned module,
  including fractional/degenerate ADSR timing, LFO blend weights and the
  first-sample phase convention, the unclamped 4x5 mod matrix, and the
  exact-rational endpoint-aligned upsampler;
- mutation gates: a clamped mod matrix, a discrete selector LFO,
  integer-sample ADSR timing, ZOH and off-endpoint upsampling must each
  fail their declared expectations;
- comparator localization: a forced mismatch localizes to its named
  module/trace with raw diagnostic artifacts kept;
- pinned-reference match (gated): with ``TORCHSYNTH_CONTROL_CAPTURE_ROOT``
  pointing at an issue-40 capture from the ratified runtime, every owned
  trace must pass the committed rubric and every mutation must fail.
"""

import hashlib
import json
import math
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import compare_control_path as comparator  # noqa: E402
from torchsynth_voice import control_path as cp  # noqa: E402
from torchsynth_voice import float_interfaces as fi  # noqa: E402
from torchsynth_voice import trace_registry as registry  # noqa: E402

OWNED_NAMES = [
    "keyboard.midi_f0",
    "keyboard.duration",
    "lfo_1_rate_adsr.output",
    "lfo_2_rate_adsr.output",
    "lfo_1_amp_adsr.output",
    "lfo_2_amp_adsr.output",
    "lfo_1.raw",
    "lfo_1.post_control_vca",
    "lfo_2.raw",
    "lfo_2.post_control_vca",
    "adsr_1.output",
    "adsr_2.output",
    "mod_matrix.vco_1_pitch",
    "mod_matrix.vco_1_amp",
    "mod_matrix.vco_2_pitch",
    "mod_matrix.vco_2_amp",
    "mod_matrix.noise_amp",
    "control_upsample.vco_1_pitch",
    "control_upsample.vco_1_amp",
    "control_upsample.vco_2_pitch",
    "control_upsample.vco_2_amp",
    "control_upsample.noise_amp",
]


def base_maps():
    document = json.loads((ROOT / "spec/reference/directed-voice-v1.json").read_text())
    normalized = {name: pair["normalized"] for name, pair in document["base"].items()}
    physical = {name: pair["physical"] for name, pair in document["base"].items()}
    return normalized, physical


def make_request(physical=None, normalized=None):
    base_normalized, base_physical = base_maps()
    noise = struct.pack("<176400f", *([0.0] * 176400))
    return fi.ResolvedRequest(
        0,
        normalized if normalized is not None else base_normalized,
        {
            "seed": 13,
            "slot": 0,
            "sample_count": 176400,
            "sha256": hashlib.sha256(noise).hexdigest(),
            "samples": noise,
        },
        physical=physical if physical is not None else base_physical,
        execution_status="canonical-batched",
    )


def render_model(physical=None, mutations=frozenset()):
    return cp.ControlPathModel(make_request(physical), mutations).render()


class OwnershipAndContractTests(unittest.TestCase):
    def test_owned_traces_match_checkpoint_map_in_registry_order(self):
        self.assertEqual(cp.owned_control_traces(), OWNED_NAMES)
        declared = cp.ControlPathModel(make_request()).declared_outputs()
        self.assertEqual(declared, OWNED_NAMES)

    def test_owned_selection_excludes_only_audio_side_and_mixer_traces(self):
        registry_order = [trace["name"] for trace in registry.load_registry()["traces"]]
        unowned = [name for name in registry_order if name not in OWNED_NAMES]
        self.assertEqual(
            sorted(unowned),
            [
                "mixer.gain",
                "mixer.output",
                "mixer.peak",
                "mixer.pre_normalization",
                "noise.post_vca",
                "noise.raw",
                "vco_1.post_vca",
                "vco_1.raw",
                "vco_2.post_vca",
                "vco_2.raw",
            ],
        )

    def test_all_five_upsample_instances_owned(self):
        owned = set(OWNED_NAMES)
        for route in registry.ROUTES:
            self.assertIn("mod_matrix." + route, owned)
            self.assertIn("control_upsample." + route, owned)

    def test_numeric_contract_stays_unbound(self):
        document = fi.load_checkpoints()
        self.assertEqual(document["numeric_contract"], "unbound:#53")
        self.assertEqual(cp.NUMERIC_CONTRACT, "unbound:#53")
        self.assertEqual(cp.CALCULATION_POLICY["numeric_contract"], "unbound:#53")
        policy = cp.CALCULATION_POLICY
        self.assertEqual(policy["calculation_dtype"], "binary64")
        self.assertEqual(
            policy["output_rounding"], "binary32-at-declared-trace-outputs"
        )

    def test_model_imports_no_torchsynth_or_torch(self):
        import importlib

        module = importlib.reload(cp)
        self.assertIsNotNone(module)
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("torchsynth", sys.modules)

    def test_model_requires_a_resolved_request_with_observed_physical(self):
        with self.assertRaises(cp.ControlPathError):
            cp.ControlPathModel(object())
        base_normalized, _ = base_maps()
        request = make_request()
        physical_free = fi.ResolvedRequest(
            0,
            base_normalized,
            request.noise,
            physical=None,
            execution_status="canonical-batched",
        )
        with self.assertRaises(cp.ControlPathError):
            cp.ControlPathModel(physical_free)

    def test_unknown_mutation_refused(self):
        with self.assertRaises(cp.ControlPathError):
            cp.ControlPathModel(make_request(), mutations={"not-a-mutation"})

    def test_keyboard_scalars_are_consumed_physical_values_verbatim(self):
        _, physical = base_maps()
        rendered = render_model(physical)
        self.assertEqual(
            rendered["keyboard.midi_f0"], [cp._f32(physical["keyboard.midi_f0"])]
        )
        self.assertEqual(
            rendered["keyboard.duration"], [cp._f32(physical["keyboard.duration"])]
        )

    def test_render_outputs_declared_lengths_and_binary32_values(self):
        rendered = render_model()
        for name, values in rendered.items():
            expected = (
                1
                if name.startswith("keyboard.")
                else (176400 if name.startswith("control_upsample.") else 1764)
            )
            self.assertEqual(len(values), expected, name)
            for value in values[:8] + values[-8:]:
                self.assertEqual(
                    struct.unpack("<f", struct.pack("<f", value))[0], value
                )


class AdsrSemanticsTests(unittest.TestCase):
    def envelope(self, prefix, **overrides):
        _, physical = base_maps()
        params = dict(physical)
        params.update({prefix + key: value for key, value in overrides.items()})
        return cp.adsr_envelope(params, prefix, params["keyboard.duration"]), params

    def test_attack_is_fractional_not_integer_samples(self):
        envelope, params = self.envelope(
            "adsr_1.", attack=0.01, decay=0.0, release=0.0, sustain=0.0, alpha=1.0
        )
        length = 0.01 * 441.0
        self.assertNotIsInstance(length, int)
        self.assertAlmostEqual(length, 4.41, places=10)
        expected_zero = cp._f32((0.0 + cp.EPS) / length + cp.EPS)
        self.assertEqual(envelope[0], expected_zero)
        expected_one = cp._f32((1.0 + cp.EPS) / length + cp.EPS)
        self.assertEqual(envelope[1], expected_one)

    def test_stage_durations_may_cut_each_other(self):
        # duration < attack: the attack is cut and decay never appears.
        _, physical = base_maps()
        params = dict(physical)
        duration = 0.05
        params["adsr_1.attack"] = 0.5
        params["adsr_1.release"] = 0.1
        params["adsr_1.sustain"] = 0.25
        params["adsr_1.alpha"] = 1.0
        envelope = cp.adsr_envelope(params, "adsr_1.", duration)
        cut = int(duration * 441)
        self.assertEqual(cut, 22)
        self.assertGreater(envelope[cut - 1], 0.4)
        # Release (44.1 control samples) has decayed the cut note away by
        # cut + 44; the whole tail is exactly silent after its end.
        self.assertLess(envelope[cut + 44], 0.1)
        self.assertEqual(envelope[cut + 45], 0.0)
        peak = max(envelope[: cut + 1])
        self.assertAlmostEqual(peak, envelope[cut], places=12)

    def test_degenerate_zero_length_stages_are_neutral_ones(self):
        envelope, _ = self.envelope(
            "adsr_2.", attack=0.0, decay=0.0, release=0.0, sustain=0.5, alpha=3.0
        )
        for value in envelope:
            self.assertEqual(value, 1.0)

    def test_full_sustain_holds_flat_through_the_sustain_region(self):
        envelope, _ = self.envelope("adsr_1.", sustain=1.0, alpha=1.0)
        for value in envelope[300:600]:
            self.assertAlmostEqual(value, 1.0, places=4)

    def test_alpha_powers_apply(self):
        envelope, params = self.envelope(
            "adsr_1.", attack=1.0, decay=0.0, release=0.0, sustain=0.0, alpha=2.0
        )
        length = 1.0 * 441.0
        expected = cp._f32(((0.0 + cp.EPS) / length + cp.EPS) ** 2.0)
        self.assertEqual(envelope[0], expected)

    def test_integer_timing_mutation_breaks_fractional_expectation(self):
        _, physical = base_maps()
        params = dict(physical)
        params.update(
            {
                "adsr_1.attack": 0.01,
                "adsr_1.decay": 2.0,
                "adsr_1.release": 2.0,
                "adsr_1.sustain": 0.0,
                "adsr_1.alpha": 1.0,
            }
        )
        envelope = cp.adsr_envelope(params, "adsr_1.", params["keyboard.duration"])
        integer = cp.adsr_envelope(
            params,
            "adsr_1.",
            params["keyboard.duration"],
            mutations={cp.MUTATION_INTEGER_ADSR_TIMING},
        )
        self.assertNotEqual(envelope, integer)


class LfoSemanticsTests(unittest.TestCase):
    def test_first_sample_phase_convention_includes_first_increment(self):
        _, physical = base_maps()
        params = dict(physical)
        params.update(
            {
                "lfo_1.frequency": 2.0,
                "lfo_1.mod_depth": 0.0,
                "lfo_1.initial_phase": 0.0,
                "lfo_1.sin": 1.0,
                "lfo_1.tri": 0.0,
                "lfo_1.saw": 0.0,
                "lfo_1.rsaw": 0.0,
                "lfo_1.sqr": 0.0,
            }
        )
        constant_rate = [2.0] * 1764
        values = cp.lfo_signal(params, "lfo_1.", constant_rate)
        argument = cp.PINNED_TWO_PI * 2.0 / 441.0
        expected = cp._f32((math.cos(argument + cp.PINNED_PI) + 1.0) / 2.0)
        self.assertEqual(values[0], expected)
        self.assertNotEqual(values[0], 0.5)

    def test_shape_weights_blend_and_never_select(self):
        params = dict(base_maps()[1])
        params.update(
            {
                "lfo_2.frequency": 1.0,
                "lfo_2.mod_depth": 0.0,
                "lfo_2.initial_phase": 0.0,
                "lfo_2.sin": 0.5,
                "lfo_2.tri": 0.5,
                "lfo_2.saw": 0.0,
                "lfo_2.rsaw": 0.0,
                "lfo_2.sqr": 0.0,
            }
        )
        rate = [1.0] * 1764
        blended = cp.lfo_signal(params, "lfo_2.", rate)
        argument = cp.PINNED_TWO_PI * 1.0 / 441.0
        sin_value, tri_value = cp._lfo_shapes(argument)[:2]
        expected = cp._f32((sin_value + tri_value) / 2.0)
        self.assertEqual(blended[0], expected)
        selector = cp.lfo_signal(
            params, "lfo_2.", rate, mutations={cp.MUTATION_SELECTOR_LFO}
        )
        self.assertNotEqual(blended, selector)

    def test_all_zero_shape_weights_are_refused_not_repaired(self):
        params = dict(base_maps()[1])
        for shape in cp.LFO_SHAPES:
            params["lfo_1." + shape] = 0.0
        with self.assertRaises(cp.UndefinedControlState):
            cp.lfo_signal(params, "lfo_1.", [1.0] * 1764)

    def test_negative_modulated_rate_clamps_at_zero(self):
        params = dict(base_maps()[1])
        params.update(
            {
                "lfo_1.frequency": 3.0,
                "lfo_1.mod_depth": -100.0,
                "lfo_1.initial_phase": 0.5,
                "lfo_1.sin": 1.0,
                "lfo_1.tri": 0.0,
                "lfo_1.saw": 0.0,
                "lfo_1.rsaw": 0.0,
                "lfo_1.sqr": 0.0,
            }
        )
        rate = [1.0] * 1764
        values = cp.lfo_signal(params, "lfo_1.", rate)
        argument = cp.PINNED_TWO_PI * 0.0 / 441.0 + 0.5
        expected = cp._f32((math.cos(argument + cp.PINNED_PI) + 1.0) / 2.0)
        self.assertEqual(values[0], expected)
        self.assertEqual(values[1], values[0])

    def test_lfo_shapes_at_known_argument(self):
        argument = 0.0
        cos_value, tri_value, saw_value, rsaw_value, square_value = cp._lfo_shapes(
            argument
        )
        self.assertAlmostEqual(cos_value, 0.0, places=12)
        self.assertEqual(square_value, 0.0)
        self.assertEqual(saw_value, 0.0)
        self.assertEqual(tri_value, 0.0)
        self.assertEqual(rsaw_value, 1.0)
        argument = cp.PINNED_PI
        cos_value, tri_value, saw_value, rsaw_value, square_value = cp._lfo_shapes(
            argument
        )
        self.assertAlmostEqual(cos_value, 1.0, places=11)
        self.assertEqual(square_value, 1.0)
        self.assertAlmostEqual(saw_value, 0.5, places=12)
        self.assertAlmostEqual(rsaw_value, 0.5, places=12)
        self.assertAlmostEqual(tri_value, 1.0, places=12)


class ModMatrixAndVcaTests(unittest.TestCase):
    def test_identity_depth_routes_one_column_unchanged(self):
        _, physical = base_maps()
        rendered = render_model(physical)
        self.assertEqual(rendered["mod_matrix.vco_1_amp"], rendered["adsr_1.output"])

    def test_matrix_outputs_are_never_clamped(self):
        columns = ([3.0] * 1764, [-3.0] * 1764, [3.0] * 1764, [-3.0] * 1764)
        params = dict(base_maps()[1])
        for route in cp.MOD_MATRIX_OUTPUTS:
            for source in cp.MOD_MATRIX_INPUTS:
                params["mod_matrix." + source + "->" + route] = 0.0
        params["mod_matrix.lfo_1->vco_1_pitch"] = 1.0
        params["mod_matrix.adsr_1->noise_amp"] = 1.0
        params["mod_matrix.adsr_2->vco_2_pitch"] = 1.0
        outputs = cp.mod_matrix(params, columns)
        self.assertEqual(max(outputs["vco_1_pitch"]), 3.0)
        self.assertEqual(max(outputs["noise_amp"]), 3.0)
        self.assertEqual(min(outputs["vco_2_pitch"]), -3.0)

    def test_input_order_is_adsr1_adsr2_lfo1_lfo2(self):
        columns = ([1.0] * 1764, [0.0] * 1764, [0.0] * 1764, [0.0] * 1764)
        params = dict(base_maps()[1])
        for route in cp.MOD_MATRIX_OUTPUTS:
            for source in cp.MOD_MATRIX_INPUTS:
                params["mod_matrix." + source + "->" + route] = 0.0
        params["mod_matrix.adsr_2->noise_amp"] = 1.0
        outputs = cp.mod_matrix(params, columns)
        self.assertEqual(outputs["noise_amp"], [0.0] * 1764)
        params["mod_matrix.adsr_2->noise_amp"] = 0.0
        params["mod_matrix.adsr_1->noise_amp"] = 2.0
        outputs = cp.mod_matrix(params, columns)
        self.assertEqual(outputs["noise_amp"], [2.0] * 1764)

    def test_control_vca_multiplies(self):
        self.assertEqual(cp.control_vca([0.5, 2.0], [4.0, -1.0]), [2.0, -2.0])
        with self.assertRaises(cp.ControlPathError):
            cp.control_vca([1.0], [1.0, 2.0])

    def test_clamp_mutation_breaks_matrix_expectation(self):
        columns = ([3.0] * 1764, [0.0] * 1764, [3.0] * 1764, [0.0] * 1764)
        params = dict(base_maps()[1])
        for route in cp.MOD_MATRIX_OUTPUTS:
            for source in cp.MOD_MATRIX_INPUTS:
                params["mod_matrix." + source + "->" + route] = 0.0
        params["mod_matrix.adsr_1->vco_1_pitch"] = 1.0
        params["mod_matrix.lfo_1->vco_1_pitch"] = 1.0
        clean = cp.mod_matrix(params, columns)
        mutated = cp.mod_matrix(
            params, columns, mutations={cp.MUTATION_CLAMP_MOD_MATRIX}
        )
        self.assertEqual(clean["vco_1_pitch"][0], 6.0)
        self.assertEqual(mutated["vco_1_pitch"][0], 1.0)


class UpsampleSemanticsTests(unittest.TestCase):
    def test_constant_column_maps_exactly(self):
        column = [0.25] * 1764
        self.assertEqual(cp.control_upsample(column), [0.25] * 176400)

    def test_endpoints_are_exact_copies(self):
        column = [cp._f32(float(i % 7) / 7.0) for i in range(1764)]
        output = cp.control_upsample(column)
        self.assertEqual(output[0], column[0])
        self.assertEqual(output[-1], column[1763])

    def test_interior_sample_matches_exact_rational_interpolation(self):
        column = [cp._f32(float((i * 37) % 101) / 101.0) for i in range(1764)]
        output = cp.control_upsample(column)
        j = 44100
        coordinate = fi.endpoint_source_coordinate(j)
        low = coordinate.numerator // coordinate.denominator
        fraction = float(coordinate - low)
        expected = cp._f32(column[low] + (column[low + 1] - column[low]) * fraction)
        self.assertEqual(output[j], expected)

    def test_zoh_control_differs_and_off_endpoint_breaks_endpoints(self):
        ramp = [cp._f32(float(i) / 1763.0) for i in range(1764)]
        linear = cp.control_upsample(ramp)
        zoh = cp.control_upsample(ramp, mode="zoh")
        self.assertNotEqual(linear, zoh)
        self.assertEqual(zoh[0], ramp[0])
        off_endpoint = cp.control_upsample(ramp, mode="off-endpoint")
        self.assertNotEqual(linear, off_endpoint)
        self.assertNotEqual(off_endpoint[-1], ramp[1763])
        with self.assertRaises(cp.ControlPathError):
            cp.control_upsample([0.0] * 10)


class MutationGateTests(unittest.TestCase):
    def test_every_declared_mutation_changes_the_render(self):
        _, physical = base_maps()
        params = dict(physical)
        for route in cp.MOD_MATRIX_OUTPUTS:
            for source in cp.MOD_MATRIX_INPUTS:
                params["mod_matrix." + source + "->" + route] = 0.0
        params["mod_matrix.adsr_1->vco_1_pitch"] = 1.0
        params["mod_matrix.lfo_1->vco_1_pitch"] = 1.0
        baseline = render_model(params)
        for mutation in cp.MUTATIONS:
            mutated = render_model(params, mutations={mutation})
            differing = [name for name in baseline if mutated[name] != baseline[name]]
            self.assertTrue(differing, mutation)

    def test_clamp_mutation_bites_only_above_unit_amplitude(self):
        baseline = render_model()
        mutated = render_model(mutations={cp.MUTATION_CLAMP_MOD_MATRIX})
        # Base physical values keep every matrix output inside [-1, 1], so the
        # clamped matrix must be ineffective there (and must not be a pass
        # elsewhere); the active case is covered by the render-level gate.
        self.assertEqual(mutated, baseline)


class ComparatorLocalizationTests(unittest.TestCase):
    def rubric_all(self, tolerance=0.0):
        limits = {
            "framing_match": comparator.Limit(1, 0, "1", "test"),
            "max_abs_error": comparator.Limit(0, tolerance, "amplitude", "test"),
        }
        return {
            name: comparator.Rubric("test-rubric", "1", dict(limits))
            for name in OWNED_NAMES
        }

    def rate_of(self, name):
        return 44100 if name.startswith("control_upsample.") else 441

    def test_forced_mismatch_localizes_and_keeps_raw_artifacts(self):
        rendered = render_model()
        perturbed = {name: list(values) for name, values in rendered.items()}
        perturbed["adsr_2.output"][900] = cp._f32(
            perturbed["adsr_2.output"][900] + 0.125
        )
        with tempfile.TemporaryDirectory() as directory:
            diagnostics = Path(directory)
            rows = comparator.compare_case(
                "synthetic-case",
                {"physical_by_name": base_maps()[1]},
                rendered,
                perturbed,
                self.rate_of,
                self.rubric_all(),
                diagnostics,
                "development",
            )
            failed = comparator.failing(rows)
            self.assertEqual([row["trace"] for row in failed], ["adsr_2.output"])
            self.assertEqual(failed[0]["module"], "adsr_2")
            artifact = diagnostics / "synthetic-case" / "adsr_2.output.json"
            self.assertTrue(artifact.exists())
            payload = json.loads(artifact.read_text())
            self.assertEqual(payload["case"]["id"], "synthetic-case")
            self.assertEqual(payload["trace"], "adsr_2.output")

    def test_exact_bytes_class_refuses_tampered_trace_files(self):
        rendered = render_model()
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory)
            record = {"traces": {}, "noise": {}}
            for name, values in rendered.items():
                payload = struct.pack("<%df" % len(values), *values)
                (case_dir / (name + ".f32le")).write_bytes(payload)
                record["traces"][name] = {
                    "file": name + ".f32le",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            noise = struct.pack("<176400f", *([0.0] * 176400))
            (case_dir / "input.noise.f32le").write_bytes(noise)
            record["noise"] = {
                "file": "input.noise.f32le",
                "sha256": hashlib.sha256(noise).hexdigest(),
            }
            (case_dir / "case.json").write_text(json.dumps(record))
            loaded_record, _, _ = comparator.load_case(case_dir)
            self.assertEqual(sorted(loaded_record["traces"]), sorted(OWNED_NAMES))
            target = case_dir / "adsr_1.output.f32le"
            target.write_bytes(target.read_bytes() + b"\x00")
            with self.assertRaises(ValueError):
                comparator.load_case(case_dir)


@unittest.skipUnless(
    os.environ.get("TORCHSYNTH_CONTROL_CAPTURE_ROOT"),
    "set TORCHSYNTH_CONTROL_CAPTURE_ROOT to an issue-40 capture for pinned match",
)
@unittest.skipUnless(
    (ROOT / "spec/reference/control-path-rubric-v1.json").exists(),
    "committed control-path rubric required",
)
class PinnedReferenceMatchTests(unittest.TestCase):
    """Full match against the pinned Voice capture under the committed rubric.

    Skipped explicitly when the capture or rubric is absent: a test that did
    not run is never reported as a pass.
    """

    CAPTURE = (
        Path(os.environ["TORCHSYNTH_CONTROL_CAPTURE_ROOT"])
        if os.environ.get("TORCHSYNTH_CONTROL_CAPTURE_ROOT")
        else None
    )

    @classmethod
    def setUpClass(cls):
        cls.rubric_document, cls.rubrics = comparator.load_rubric(
            ROOT / "spec/reference/control-path-rubric-v1.json"
        )

    def rate_of(self, name):
        return 44100 if name.startswith("control_upsample.") else 441

    def load_manifest(self):
        manifest = json.loads((self.CAPTURE / "manifest.json").read_text())
        self.assertEqual(manifest["runtime_profile"], "release-mkl-compatible-v1")
        return manifest

    def test_every_captured_case_passes_the_committed_rubric(self):
        manifest = self.load_manifest()
        failures = []
        endpoints_exact = True
        for item in manifest["cases"]:
            safe = item["case_id"].replace(":", "_")
            record, reference, noise = comparator.load_case(
                self.CAPTURE / "cases" / safe
            )
            model_outputs = cp.ControlPathModel(
                comparator.build_request(record, noise)
            ).render()
            rows = comparator.compare_case(
                item["case_id"],
                record,
                reference,
                model_outputs,
                self.rate_of,
                self.rubrics,
                None,
                "development",
            )
            failures.extend(comparator.failing(rows))
            endpoints = comparator.endpoint_report(reference, model_outputs)
            endpoints_exact = endpoints_exact and all(
                item["reference_first_last_equal"] and item["model_first_last_equal"]
                for item in endpoints.values()
            )
        self.assertEqual(failures, [])
        self.assertTrue(endpoints_exact)

    def test_preregistered_mutations_fail_the_pinned_match(self):
        manifest = self.load_manifest()
        cases = [item for item in manifest["cases"] if item["kind"] == "development"][
            : comparator.MUTATION_CASES
        ]
        loaded = []
        for item in cases:
            safe = item["case_id"].replace(":", "_")
            record, reference, noise = comparator.load_case(
                self.CAPTURE / "cases" / safe
            )
            loaded.append((item["case_id"], record, reference, noise))
        results = comparator.run_mutations(loaded, self.rate_of, self.rubrics, None)
        for result in results:
            self.assertTrue(
                result["failed"],
                "mutation did not fail the pinned match: " + result["mutation"],
            )


if __name__ == "__main__":
    unittest.main()
