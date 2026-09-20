"""Independent float Voice composition: end-to-end conformance model.

Issue #43 deliverable. Composes the three landed independent float models —
the control path (#40, ``torchsynth_voice.control_path``), the sources
(#41, ``torchsynth_voice.float_sources``) and the mix chain (#42,
``torchsynth_voice.float_mix``) — into one whole-voice render driven by a
single resolved request, hitting every checkpoint of the landed map in
``reference/float-checkpoints-v1.json`` (32 named traces in registry order
plus the three resolved inputs). Stdlib-only; imports no TorchSynth, Torch or
NumPy; deterministic per render; no state across renders.

Composition, not re-proof: the DSP belongs to the landed modules and is
called unchanged. This module owns only the cross-module wiring — which
buffer each module consumes and produces at the declared seams — and the
composed-level fault-injection controls for that wiring. The per-module
matches are settled evidence (#129, #130, #131); the conformance question
answered here is whether the composed chain stays conformant when the
modules are fed each other's outputs instead of captured reference
buffers.

Declared composed calculation policy (``CALCULATION_POLICY``, version
``float-voice-v1``): the composed render is the staged function composition
of the landed per-module policies in evaluation order — control-rate stage
(binary64 internal, binary32 trace outputs), source stage (elementwise
binary32 upstream op order, unbounded binary64 phase accumulator), mix
stage (elementwise binary32 VCA products, ordered binary64 mixer sum
rounded once, exact whole-clip peak, strict ``peak > 1``). Numeric formats
for a later fixed model stay open: the checkpoint map carries
``numeric_contract = "unbound:#53"`` and DR-0008 remains Proposed.

Composed-level mutations are preregistered cross-module wiring faults. Each
must fail its named composed comparison rows and localize to its boundary;
a mutation that does not fail is a defect of the control, not a pass:

- ``swapped-vco-pitch``: each VCO consumes the other's upsampled pitch
  column (control_upsample.vco_2_pitch into vco_1#1 and vice versa). Must
  fail the vco_*.raw rows and everything downstream while the
  control_upsample.* rows still match, localizing to the
  control->sources pitch wiring.
- ``lfo-adsr-control-swap``: the modulation matrix consumes the post-VCA
  LFO columns in the main-ADSR input slots and the main ADSR columns in
  the post-VCA LFO slots (control-rate confusion between the LFO/ADSR
  paths). Must fail the mod_matrix.* rows and everything downstream while
  all four source envelopes still match, localizing to the mod-matrix
  input wiring.
- ``normalize-before-mix``: the whole-clip normalization division is
  applied to the sources before the mixer instead of to the mix after it.
  Must fail the mixer.peak/mixer.gain rows and the branch/output relations
  on an above-one case while every pre-mix row still matches, localizing
  to the normalization placement.
"""

from __future__ import annotations

from . import float_interfaces as fi
from . import float_mix as fm
from . import float_sources as fs
from .control_path import (
    ControlPathModel,
    MOD_MATRIX_OUTPUTS,
    control_upsample,
    mod_matrix,
)
from .float_sources import NoiseSource, SineVCO, SquareSawVCO

SOURCE_COMMIT = fi.SOURCE_COMMIT
AUDIO_SAMPLES = fi.AUDIO_SAMPLES
CONTROL_SAMPLES = fi.CONTROL_SAMPLES

CALCULATION_POLICY = fi.CalculationPolicy(
    calculation_dtype="float32",
    operation_policy=(
        "float-voice-v1: staged composition of the landed per-module "
        "policies in evaluation order (control-path float-internal-v1, "
        "float-sources-v1, float-mix-v1); the composition owns cross-module "
        "wiring only and re-derives no DSP"
    ),
    version="float-voice-v1",
)

MUTATION_SWAPPED_VCO_PITCH = "swapped-vco-pitch"
MUTATION_LFO_ADSR_SWAP = "lfo-adsr-control-swap"
MUTATION_NORMALIZE_BEFORE_MIX = "normalize-before-mix"
MUTATIONS = (
    MUTATION_SWAPPED_VCO_PITCH,
    MUTATION_LFO_ADSR_SWAP,
    MUTATION_NORMALIZE_BEFORE_MIX,
)

MUTATION_BOUNDARIES = {
    MUTATION_SWAPPED_VCO_PITCH: "control_upsample->vco pitch input wiring",
    MUTATION_LFO_ADSR_SWAP: "mod_matrix control-rate input wiring",
    MUTATION_NORMALIZE_BEFORE_MIX: "normalization placement around the mixer",
}

MUTATION_FAILED_TRACES = {
    MUTATION_SWAPPED_VCO_PITCH: (
        "vco_1.raw",
        "vco_2.raw",
        "vco_1.post_vca",
        "vco_2.post_vca",
        "mixer.pre_normalization",
        "mixer.peak",
        "mixer.gain",
        "mixer.output",
    ),
    MUTATION_LFO_ADSR_SWAP: tuple(
        ["mod_matrix." + route for route in MOD_MATRIX_OUTPUTS]
        + ["control_upsample." + route for route in MOD_MATRIX_OUTPUTS]
        + [
            "vco_1.raw",
            "vco_2.raw",
            "vco_1.post_vca",
            "vco_2.post_vca",
            "noise.post_vca",
            "mixer.pre_normalization",
            "mixer.peak",
            "mixer.gain",
            "mixer.output",
        ]
    ),
    MUTATION_NORMALIZE_BEFORE_MIX: (
        "mixer.pre_normalization",
        "mixer.peak",
        "mixer.gain",
        "mixer.output",
    ),
}

MUTATION_INTACT_TRACES = {
    MUTATION_SWAPPED_VCO_PITCH: (
        "control_upsample.vco_1_pitch",
        "control_upsample.vco_2_pitch",
        "control_upsample.vco_1_amp",
        "control_upsample.vco_2_amp",
        "control_upsample.noise_amp",
    ),
    MUTATION_LFO_ADSR_SWAP: (
        "lfo_1.raw",
        "lfo_2.raw",
        "lfo_1.post_control_vca",
        "lfo_2.post_control_vca",
        "adsr_1.output",
        "adsr_2.output",
    ),
    MUTATION_NORMALIZE_BEFORE_MIX: (
        "vco_1.raw",
        "vco_2.raw",
        "noise.raw",
    ),
}


class FloatVoiceError(ValueError):
    """Raised for contract violations in the composed Voice model."""


def voice_checkpoints():
    """Every declared checkpoint output in registry (evaluation) order."""

    document = fi.load_checkpoints()
    names = []
    for entry in document["checkpoints"]:
        names.extend(entry["outputs"])
    if len(names) != 32 or len(set(names)) != 32:
        raise FloatVoiceError(
            "checkpoint map must declare 32 unique trace outputs"
        )
    return names


def resolved_input_names():
    """The three resolved input checkpoints, in map order."""

    document = fi.load_checkpoints()
    return [entry["name"] for entry in document["resolved_inputs"]]


def f32le_bytes(samples):
    """Encode binary32 samples as the declared little-endian buffer bytes."""

    return fm.f32le_bytes(samples)


def f32le_values(data):
    """Decode declared little-endian binary32 buffer bytes."""

    return fm.f32le_values(data)


class FloatVoiceModel:
    """The composed whole-voice model for one resolved sound.

    Constructed only from an already-resolved request whose ``physical``
    map is the observed runtime conversion. ``render()`` returns every
    declared checkpoint keyed by its registry name, in registry order,
    plus a ``diagnostics`` mapping (call-order wiring facts, branch
    decision, peak index) that is not part of the checkpoint set.
    """

    def __init__(self, request, mutations=frozenset()):
        if not isinstance(request, fi.ResolvedRequest):
            raise FloatVoiceError(
                "the composed model requires a resolved request; use"
                " float_interfaces.ResolvedRequest"
            )
        if request.physical is None:
            raise FloatVoiceError(
                "physical.parameters is a measured seam: the observed runtime"
                " conversion must be supplied, never derived"
            )
        unknown = set(mutations) - set(MUTATIONS)
        if unknown:
            raise FloatVoiceError(
                "unknown composed mutations: " + ",".join(sorted(unknown))
            )
        self.request = request
        self.mutations = frozenset(mutations)
        self.params = request.physical

    def declared_outputs(self):
        """Every composed checkpoint name in registry order."""

        return voice_checkpoints()

    def render(self):
        """Compose the full Voice; return ``{checkpoint name: values}``.

        The stages call the landed modules unchanged and wire their
        declared outputs to the declared inputs. ``diagnostics`` records
        the wiring decisions a consumer needs for localization; it never
        carries a checkpoint value.
        """

        params = self.params
        mutations = self.mutations

        control = ControlPathModel(self.request).render()

        adsr_signals = (control["adsr_1.output"], control["adsr_2.output"])
        lfo_signals = (
            control["lfo_1.post_control_vca"],
            control["lfo_2.post_control_vca"],
        )
        matrix_signals = adsr_signals + lfo_signals
        if MUTATION_LFO_ADSR_SWAP in mutations:
            # Composed wiring fault: the matrix consumes the control-rate
            # columns from the wrong producers (LFO paths where the main
            # ADSRs belong and vice versa). Must fail the mod_matrix rows
            # and everything downstream; the four envelope traces stay
            # intact, so the divergence localizes to the matrix inputs.
            matrix_signals = lfo_signals + adsr_signals
        columns = mod_matrix(params, matrix_signals)
        upsampled = {
            route: control_upsample(columns[route]) for route in MOD_MATRIX_OUTPUTS
        }
        if MUTATION_LFO_ADSR_SWAP not in mutations:
            # Wiring self-check: with the matrix inputs wired as declared,
            # the composed matrix must reproduce the landed control-path
            # model's own matrix byte-for-byte.
            for route in MOD_MATRIX_OUTPUTS:
                if columns[route] != control["mod_matrix." + route]:
                    raise FloatVoiceError(
                        "composed matrix wiring diverges from the landed"
                        " control path: " + route
                    )

        noise_bytes = NoiseSource.output(self.request.noise["samples"])
        noise = list(f32le_values(noise_bytes))

        vco_1_pitch = upsampled["vco_1_pitch"]
        vco_2_pitch = upsampled["vco_2_pitch"]
        midi_f0 = control["keyboard.midi_f0"][0]
        if MUTATION_SWAPPED_VCO_PITCH in mutations:
            # Composed wiring fault: each oscillator consumes the other's
            # upsampled pitch column. The upsampled columns themselves stay
            # intact, so the divergence localizes to the pitch wiring.
            vco_1_pitch, vco_2_pitch = vco_2_pitch, vco_1_pitch
        vco_1 = SineVCO(
            params["vco_1.tuning"],
            params["vco_1.mod_depth"],
            params["vco_1.initial_phase"],
        ).output(midi_f0, vco_1_pitch)
        vco_2 = SquareSawVCO(
            params["vco_2.tuning"],
            params["vco_2.mod_depth"],
            params["vco_2.initial_phase"],
            params["vco_2.shape"],
        ).output(midi_f0, vco_2_pitch)

        levels = (
            params["mixer.vco_1"],
            params["mixer.vco_2"],
            params["mixer.noise"],
        )
        amps = (
            upsampled["vco_1_amp"],
            upsampled["vco_2_amp"],
            upsampled["noise_amp"],
        )
        if MUTATION_NORMALIZE_BEFORE_MIX in mutations:
            # Composed wiring fault: the whole-clip normalization division
            # applied to the sources between the oscillator outputs and the
            # VCAs, instead of to the mixed sum after the mixer. The
            # observed oscillator traces stay intact — the divided signals
            # only enter the VCA — so the divergence localizes to the
            # normalization placement.
            whole_peak, _ = fm.peak_of_clip(
                fm.mixer_weighted_sum(
                    [
                        fm.audio_vca(vco_1, amps[0]),
                        fm.audio_vca(vco_2, amps[1]),
                        fm.audio_vca(noise, amps[2]),
                    ],
                    levels,
                )
            )
            source_1, source_2, source_noise = vco_1, vco_2, noise
            if whole_peak > 1.0:
                source_1 = [fm.f32(value / whole_peak) for value in vco_1]
                source_2 = [fm.f32(value / whole_peak) for value in vco_2]
                source_noise = [fm.f32(value / whole_peak) for value in noise]
            post_vca = [
                fm.audio_vca(source_1, amps[0]),
                fm.audio_vca(source_2, amps[1]),
                fm.audio_vca(source_noise, amps[2]),
            ]
            pre = fm.mixer_weighted_sum(post_vca, levels)
            peak, index = fm.peak_of_clip(pre)
            rendered = {
                "vco_1.post_vca": post_vca[0],
                "vco_2.post_vca": post_vca[1],
                "noise.post_vca": post_vca[2],
                "mixer.pre_normalization": pre,
                "mixer.peak": [peak],
                "mixer.gain": [fm.derived_gain(peak)],
                "mixer.output": list(pre),
            }
            branch = peak > 1.0
        else:
            rendered, mix_diagnostics = fm.render_mix_chain(
                (vco_1, vco_2, noise), amps, levels
            )
            branch = mix_diagnostics["normalized_branch"]
            index = mix_diagnostics["peak_index"]

        composed = dict(control)
        composed.update(rendered)
        composed["vco_1.raw"] = vco_1
        composed["vco_2.raw"] = vco_2
        composed["noise.raw"] = noise
        for route in MOD_MATRIX_OUTPUTS:
            composed["mod_matrix." + route] = columns[route]
            composed["control_upsample." + route] = upsampled[route]

        declared = self.declared_outputs()
        missing = [name for name in declared if name not in composed]
        extra = [name for name in composed if name not in declared]
        if missing or extra:
            raise FloatVoiceError(
                "composed checkpoint set mismatch; missing="
                + ",".join(missing)
                + " extra="
                + ",".join(extra)
            )
        ordered = {name: composed[name] for name in declared}
        diagnostics = {
            "mutation_boundaries": {
                mutation: MUTATION_BOUNDARIES[mutation]
                for mutation in sorted(mutations)
            },
            "normalized_branch": branch,
            "peak_index": index,
            "noise_sha256": self.request.noise["sha256"],
        }
        return ordered, diagnostics


def diverged_boundary(reference, candidate, order=None):
    """First checkpoint, in evaluation order, where composition diverges.

    ``reference`` and ``candidate`` map checkpoint names to sample lists.
    Returns ``None`` when every referenced checkpoint is exactly equal;
    otherwise the tuple ``(checkpoint, module, boundary)`` naming where the
    divergence entered the composed chain. Scalars and buffers compare
    sample-by-sample exactly — the declared-metrics numeric rows are
    decided by the comparator's rubrics, not here. Checkpoints without a
    reference entry are skipped, so localization works against whatever
    pinned seams a case retains.
    """

    if order is None:
        order = voice_checkpoints()
    for name in order:
        if name not in candidate:
            return (name, name.split(".")[0], "missing from composed render")
        if name not in reference:
            continue
        mine, theirs = candidate[name], reference[name]
        if len(mine) != len(theirs) or any(
            a != b for a, b in zip(mine, theirs)
        ):
            return (name, name.split(".")[0], "declared checkpoint divergence")
    return None


def checkpoint_shape_of(name, values):
    """The declared registry shape for one composed checkpoint."""

    if name in ("mixer.peak", "mixer.gain"):
        return [1]
    if name in ("keyboard.midi_f0", "keyboard.duration"):
        return []
    return [len(values)]
