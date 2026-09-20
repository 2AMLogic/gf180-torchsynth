"""Independent float control path for the default Voice (issue #40).

Stdlib-only reference model of the pinned Voice's control path at
``2b0964d4c6c3d472a2a0d54d91b408caaeffca6d``: keyboard, the six ADSRs, the
two LFOs, the two control VCAs, the 4x5 modulation matrix, and the five
endpoint-aligned control upsamplers (checkpoint orders 1-13 plus the
shared upsampler instances consumed by the later audio path).

Import discipline: this module imports no TorchSynth, Torch or NumPy and
performs no render against the pinned source. ``physical.parameters`` is a
measured seam (``spec/FLOAT-INTERFACES.md``): the model consumes observed
runtime conversions verbatim and implements no parameter conversion at all,
so an analytic substitution is structurally impossible here.

Numeric contract: ``unbound:#53``. DR-0008 is Proposed and none of its
choices is accepted; no fixed-point width, scaling or rounding appears in
this module. The declared float calculation policy is binary64 internals
with binary32 rounding at every declared trace output
(``spec/CONTROL-PATH.md``). It never presents implicit binary64 results as
original binary32: every returned trace value is rounded through binary32.

Declared semantics mirrored from the pinned source (``torchsynth/module.py``,
``torchsynth/synth.py`` at the pin):

- ADSR stage durations are fractional control samples
  (``seconds * control_rate``, never rounded); note-off is known in
  advance; the attack/decay cut follows ``new_attack = min(attack, dur)``,
  ``new_decay = clamp(dur - attack, 0, decay)``.
- LFO phase accumulates the first frequency increment before
  ``initial_phase`` is added (first-sample phase convention, sample zero is
  not the bare initial phase); shape weights are a continuous blend
  (``w^e / sum``), never a discrete selector; an all-zero weight state is
  refused as undefined, never silently repaired.
- The modulation matrix is a plain 4x5 weighted sum with input order main
  ADSR1, main ADSR2, post-VCA LFO1, post-VCA LFO2 and **no clamp**.
- Upsampling is endpoint-aligned linear interpolation reading source
  coordinate ``j*(1764-1)/(176400-1)`` as an exact rational; endpoints
  coincide with control indices 0 and 1763.
"""

import math
import struct

from .float_interfaces import (
    AUDIO_SAMPLES,
    CONTROL_SAMPLES,
    InterfaceError,
    NUMERIC_CONTRACT,
    ResolvedRequest,
    endpoint_source_coordinate,
    load_checkpoints,
)

__all__ = [
    "CALCULATION_POLICY",
    "ControlPathModel",
    "ControlPathError",
    "UndefinedControlState",
    "owned_control_traces",
    "adsr_envelope",
    "lfo_signal",
    "control_vca",
    "mod_matrix",
    "control_upsample",
    "MUTATIONS",
    "PINNED_TWO_PI",
    "PINNED_PI",
    "LFO_EXPONENT",
    "EPS",
]

CALCULATION_POLICY = {
    "calculation_dtype": "binary64",
    "output_rounding": "binary32-at-declared-trace-outputs",
    "operation_policy": "spec/CONTROL-PATH.md float control policy v1",
    "version": "float-control-path-v1",
    "numeric_contract": NUMERIC_CONTRACT,
}

# Pinned upstream constants: torch.pi is the binary32 rounding of pi and the
# LFO soft selector exponent is the literal 2.718281828, not math.e.
PINNED_PI = 3.1415927410125732
PINNED_TWO_PI = 2.0 * PINNED_PI
LFO_EXPONENT = 2.718281828
EPS = 1e-6

MUTATION_CLAMP_MOD_MATRIX = "clamp-mod-matrix"
MUTATION_SELECTOR_LFO = "selector-lfo"
MUTATION_INTEGER_ADSR_TIMING = "integer-adsr-timing"
MUTATIONS = (
    MUTATION_CLAMP_MOD_MATRIX,
    MUTATION_SELECTOR_LFO,
    MUTATION_INTEGER_ADSR_TIMING,
)

CONTROL_RATE_HZ = 441.0


class ControlPathError(InterfaceError):
    """Actionable control-path failure: shape, state or mutation misuse."""


class UndefinedControlState(ControlPathError):
    """A declared-undefined numeric state (for example all-zero LFO weights)."""


def _f32(value):
    """Round one binary64 value to the declared binary32 trace precision."""

    return struct.unpack("<f", struct.pack("<f", value))[0]


def _f32_list(values):
    return [_f32(value) for value in values]


def owned_control_traces(checkpoints=None, registry=None):
    """The declared control-path trace names in registry/evaluation order.

    Ownership follows the checkpoint map: orders 1-13 plus the remaining
    shared ``control_upsample`` instances (orders 15, 17, 19, 22). VCO, VCA,
    noise and mixer traces belong to #41/#42/#43 and are excluded here.
    """

    document = checkpoints if checkpoints is not None else load_checkpoints(registry)
    owned = []
    for entry in document["checkpoints"]:
        module = entry["module"]
        if entry["order"] > 13 and module != "control_upsample":
            continue
        owned.extend(entry["outputs"])
    return owned


def _duration_samples(seconds, mutations):
    """Pinned seconds->control-samples conversion; possibly fractional.

    The integer-timing mutation is a negative control only: it must fail the
    match, never silently replace the declared fractional semantics.
    """

    if MUTATION_INTEGER_ADSR_TIMING in mutations:
        return float(int(seconds * CONTROL_RATE_HZ))
    return seconds * CONTROL_RATE_HZ


def _ramp(length, start, inverse, alpha):
    """One pinned ADSR ramp row of ``CONTROL_SAMPLES`` binary64 values.

    ``length``/``start`` are in fractional control samples. Mirrors the
    pinned ``ADSR.ramp``: tilt by ``start``, clamp at zero, scale by
    ``(x + eps) / length + eps``, clamp at one, conditional inversion for
    positive lengths, then the alpha power. A zero length yields the all-one
    ramp exactly like the pinned binary32 division by zero (inf clamps to
    one), both forward and inverted.
    """

    row = []
    for index in range(CONTROL_SAMPLES):
        value = float(index) - start
        if value < 0.0:
            value = 0.0
        if length == 0.0:
            value = 1.0
        else:
            value = (value + EPS) / length + EPS
            if value > 1.0:
                value = 1.0
            if inverse and length > 0.0:
                value = 1.0 - value
        row.append(value**alpha)
    return row


def adsr_envelope(params, prefix, duration_seconds, mutations=frozenset()):
    """One pinned ADSR envelope as ``CONTROL_SAMPLES`` binary32 values."""

    duration = duration_seconds
    attack = params[prefix + "attack"]
    decay = params[prefix + "decay"]
    sustain = params[prefix + "sustain"]
    alpha = params[prefix + "alpha"]
    duration_samples = _duration_samples(duration, mutations)
    new_attack_samples = _duration_samples(min(attack, duration), mutations)
    decay_seconds = max(duration - attack, 0.0)
    new_decay_samples = _duration_samples(min(decay_seconds, decay), mutations)
    attack_signal = _ramp(new_attack_samples, 0.0, False, alpha)
    decay_signal = _ramp(new_decay_samples, new_attack_samples, True, alpha)
    release_samples = _duration_samples(params[prefix + "release"], mutations)
    release_signal = _ramp(release_samples, duration_samples, True, alpha)
    envelope = []
    for attack_value, decay_value, release_value in zip(
        attack_signal, decay_signal, release_signal
    ):
        decay_factor = (1.0 - sustain) * decay_value + sustain
        envelope.append(_f32(attack_value * decay_factor * release_value))
    if len(envelope) != CONTROL_SAMPLES:
        raise ControlPathError("adsr envelope must fill the control buffer")
    return envelope


def _lfo_shapes(argument):
    """The five pinned LFO shapes at one phase argument."""

    cos_argument = math.cos(argument + PINNED_PI)
    if cos_argument > 0.0:
        sign = 1.0
    elif cos_argument < 0.0:
        sign = -1.0
    else:
        sign = 0.0
    cos_value = (cos_argument + 1.0) / 2.0
    square_value = (sign + 1.0) / 2.0
    saw_value = math.fmod(argument, PINNED_TWO_PI)
    if saw_value < 0.0:
        saw_value += PINNED_TWO_PI
    saw_value = saw_value / PINNED_TWO_PI
    reverse_value = 1.0 - saw_value
    triangle_value = 2.0 * saw_value
    if triangle_value > 1.0:
        triangle_value = 2.0 - triangle_value
    return cos_value, triangle_value, saw_value, reverse_value, square_value


LFO_SHAPES = ("sin", "tri", "saw", "rsaw", "sqr")


def lfo_signal(params, prefix, rate_envelope, mutations=frozenset()):
    """One pinned LFO as ``CONTROL_SAMPLES`` binary32 values.

    ``rate_envelope`` is the rate-ADSR output (frequency-envelope role) in
    Hz. The frequency path clamps the modulated rate at zero before phase
    accumulation; the phase accumulates the first increment before the
    initial phase is added.
    """

    frequency = params[prefix + "frequency"]
    mod_depth = params[prefix + "mod_depth"]
    initial_phase = params[prefix + "initial_phase"]
    weights = []
    if MUTATION_SELECTOR_LFO in mutations:
        # Negative control: discrete argmax selector instead of the pinned
        # continuous blend. One-hot by construction; must fail the match.
        for shape in LFO_SHAPES:
            weights.append(1.0 if shape == "sin" else 0.0)
    else:
        for shape in LFO_SHAPES:
            weights.append(params[prefix + shape] ** LFO_EXPONENT)
    total = math.fsum(weights)
    if total == 0.0:
        raise UndefinedControlState(
            "undefined LFO shape state: all-zero shape weights for " + prefix
        )
    weights = [weight / total for weight in weights]

    phase = 0.0
    values = []
    for index in range(CONTROL_SAMPLES):
        rate = frequency + mod_depth * rate_envelope[index]
        if rate < 0.0:
            rate = 0.0
        phase += PINNED_TWO_PI * rate / CONTROL_RATE_HZ
        argument = phase + initial_phase
        shapes = _lfo_shapes(argument)
        blended = math.fsum(weight * shape for weight, shape in zip(weights, shapes))
        values.append(_f32(blended))
    return values


def control_vca(waveform, gain):
    """Pinned control-rate VCA: elementwise product, binary32 outputs."""

    if len(waveform) != len(gain):
        raise ControlPathError("control VCA arguments must have equal length")
    return [_f32(wave * gain_value) for wave, gain_value in zip(waveform, gain)]


def _modulation_inputs(signals):
    columns = list(signals)
    if len(columns) != 4 or any(len(column) != CONTROL_SAMPLES for column in columns):
        raise ControlPathError("mod matrix requires four control-rate input columns")
    return columns


MOD_MATRIX_OUTPUTS = (
    "vco_1_pitch",
    "vco_1_amp",
    "vco_2_pitch",
    "vco_2_amp",
    "noise_amp",
)
MOD_MATRIX_INPUTS = ("adsr_1", "adsr_2", "lfo_1", "lfo_2")


def mod_matrix(params, signals, mutations=frozenset()):
    """The pinned 4x5 modulation matrix; no clamp at any output.

    ``params`` is the observed physical map; depths are read by the exact
    canonical ``mod_matrix.<input>-><output>`` names. ``signals`` is ordered
    main ADSR1, main ADSR2, post-VCA LFO1, post-VCA LFO2.
    """

    columns = _modulation_inputs(signals)
    outputs = {}
    for route in MOD_MATRIX_OUTPUTS:
        merged = []
        for index, source in enumerate(MOD_MATRIX_INPUTS):
            depth = params["mod_matrix." + source + "->" + route]
            column = columns[index]
            if index == 0:
                merged = [depth * value for value in column]
            else:
                merged = [total + depth * value for total, value in zip(merged, column)]
        if MUTATION_CLAMP_MOD_MATRIX in mutations:
            # Negative control: clamped matrix outputs. Must fail the match.
            merged = [
                -1.0 if value < -1.0 else (1.0 if value > 1.0 else value)
                for value in merged
            ]
        outputs[route] = _f32_list(merged)
    return outputs


def control_upsample(column, mode="linear"):
    """Endpoint-aligned upsample of one control column to audio length.

    Output index ``j`` reads the exact rational source coordinate
    ``j*(1764-1)/(176400-1)``; the endpoints are exact copies of control
    indices 0 and 1763. ``mode="zoh"`` and ``mode="off-endpoint"`` (the
    dropped-endpoint scale ``j*1763/176400``) are preregistered negative
    controls only; both must fail the declared match, and ``off-endpoint``
    must additionally fail the exact-endpoint contract.
    """

    if len(column) != CONTROL_SAMPLES:
        raise ControlPathError(
            "control_upsample requires the full 1764-sample control column"
        )
    output = []
    if mode == "zoh":
        for j in range(AUDIO_SAMPLES):
            output.append(column[int(endpoint_source_coordinate(j))])
        return output
    if mode == "off-endpoint":
        for j in range(AUDIO_SAMPLES):
            coordinate = j * (CONTROL_SAMPLES - 1) / AUDIO_SAMPLES
            low = int(coordinate)
            fraction = coordinate - low
            if low + 1 >= CONTROL_SAMPLES or fraction == 0:
                value = float(column[low])
            else:
                value = (
                    float(column[low])
                    + (float(column[low + 1]) - float(column[low])) * fraction
                )
            output.append(_f32(value))
        return output
    if mode != "linear":
        raise ControlPathError("unknown control_upsample mode: " + mode)
    numerator_unit = CONTROL_SAMPLES - 1
    denominator = AUDIO_SAMPLES - 1
    for probe in (0, 1, AUDIO_SAMPLES // 2, AUDIO_SAMPLES - 1):
        if (
            float(endpoint_source_coordinate(probe))
            != probe * numerator_unit / denominator
        ):
            raise ControlPathError(
                "upsample coordinate form diverges from the declared contract"
            )
    # Exact integer form of endpoint_source_coordinate(j): the coordinate is
    # j*1763/176399; low = floor(coordinate); the fractional part is
    # remainder/176399. Declared endpoints: j=0 -> control 0, j=176399 -> 1763.
    output = []
    for j in range(AUDIO_SAMPLES):
        numerator = j * numerator_unit
        low = numerator // denominator
        remainder = numerator - low * denominator
        if remainder == 0:
            value = float(column[low])
        else:
            left = float(column[low])
            right = float(column[low + 1])
            value = left + (right - left) * (remainder / denominator)
        output.append(_f32(value))
    if output[0] != column[0] or output[-1] != column[-1]:
        raise ControlPathError("endpoint-aligned upsample endpoints must be exact")
    return output


class ControlPathModel:
    """The declared control path for one resolved sound.

    Constructed only from an already-resolved request whose ``physical`` map
    is the observed runtime conversion (measured-observation class). The
    model consumes those values verbatim: there is no conversion routine in
    this module to substitute. Rendering produces every declared control
    trace in registry order, keyed by its exact registry name.
    """

    def __init__(self, request, mutations=frozenset()):
        if not isinstance(request, ResolvedRequest):
            raise ControlPathError(
                "control path requires a resolved request; use"
                " float_interfaces.ResolvedRequest"
            )
        if request.physical is None:
            raise ControlPathError(
                "physical.parameters is a measured seam: the observed runtime"
                " conversion must be supplied, never derived"
            )
        unknown = set(mutations) - set(MUTATIONS)
        if unknown:
            raise ControlPathError("unknown mutations: " + ",".join(sorted(unknown)))
        self.request = request
        self.mutations = frozenset(mutations)
        self.params = request.physical
        self.checkpoints = load_checkpoints()

    def declared_outputs(self):
        """Every owned control trace name in registry order."""

        return owned_control_traces(self.checkpoints)

    def render(self):
        """Render the control path; return ``{trace name: values}``.

        Keyboard scalars are single-value lists of the consumed physical
        values; buffers are binary32 lists at their declared lengths. Output
        order is the checkpoint/registry order.
        """

        params = self.params
        mutations = self.mutations
        midi_f0 = [_f32(params["keyboard.midi_f0"])]
        duration = [_f32(params["keyboard.duration"])]
        duration_seconds = params["keyboard.duration"]

        rate_1 = adsr_envelope(params, "lfo_1_rate_adsr.", duration_seconds, mutations)
        rate_2 = adsr_envelope(params, "lfo_2_rate_adsr.", duration_seconds, mutations)
        amp_1 = adsr_envelope(params, "lfo_1_amp_adsr.", duration_seconds, mutations)
        amp_2 = adsr_envelope(params, "lfo_2_amp_adsr.", duration_seconds, mutations)

        lfo_1 = lfo_signal(params, "lfo_1.", rate_1, mutations)
        lfo_2 = lfo_signal(params, "lfo_2.", rate_2, mutations)
        post_1 = control_vca(lfo_1, amp_1)
        post_2 = control_vca(lfo_2, amp_2)

        adsr_1 = adsr_envelope(params, "adsr_1.", duration_seconds, mutations)
        adsr_2 = adsr_envelope(params, "adsr_2.", duration_seconds, mutations)

        columns = mod_matrix(params, (adsr_1, adsr_2, post_1, post_2), mutations)

        upsampled = {
            route: control_upsample(columns[route]) for route in MOD_MATRIX_OUTPUTS
        }

        rendered = {
            "keyboard.midi_f0": midi_f0,
            "keyboard.duration": duration,
            "lfo_1_rate_adsr.output": rate_1,
            "lfo_2_rate_adsr.output": rate_2,
            "lfo_1_amp_adsr.output": amp_1,
            "lfo_2_amp_adsr.output": amp_2,
            "lfo_1.raw": lfo_1,
            "lfo_1.post_control_vca": post_1,
            "lfo_2.raw": lfo_2,
            "lfo_2.post_control_vca": post_2,
            "adsr_1.output": adsr_1,
            "adsr_2.output": adsr_2,
        }
        for route in MOD_MATRIX_OUTPUTS:
            rendered["mod_matrix." + route] = columns[route]
            rendered["control_upsample." + route] = upsampled[route]
        declared = self.declared_outputs()
        missing = [name for name in declared if name not in rendered]
        if missing:
            raise ControlPathError(
                "render missing declared traces: " + ",".join(missing)
            )
        return {name: rendered[name] for name in declared}
