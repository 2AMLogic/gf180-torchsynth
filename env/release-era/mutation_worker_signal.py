"""Signal-family runtime applicator dispatch for the pinned worker (#336).

Family-owned adjacent module of ``env/release-era/mutation_worker.py``
(Python 3.9, release image ``release-mkl-compatible-v1``). The bridge worker
stays byte-identical (its digest is bound by the committed runtime
publication and by the campaign manifest); this module extends it by
subclassing and by calling its schedule validator, never by editing it:

* ``validate_signal_schedule`` first runs the landed
  ``mutation_worker.validate_schedule`` (schedule kind, pinned batch, slot
  bounds, inventory names, registry module/occurrence association, declared
  order, parameter-before-module causality) and then fails closed before any
  render on a signal schedule outside the declared allowlist: manifest
  identity/revision/frozen digest and file digest, the resolved case and batch
  slot, the ``signal_voice_ops.RUNTIME_OPERATORS`` allowlist (``norm.*``,
  ``bridge.*`` and unknown ids refuse), magnitudes, frames and composition.
* ``SignalMutationSession`` dispatches the family applicators inside the
  existing scoped session: passive observers snapshot first, output
  replacements are written in place into the declared slot of the same tensor
  object (the #22 CallTracker association is kept), source-parameter faults
  are applied at the keyboard anchor with the unit conversion recorded, every
  application is an ordered in-band event with original/replacement digests,
  consumed replacement bytes are retained separately from the passive original
  capture, and all hooks/parameters are restored after success, errors and
  partial setup.
* ``SignalHarness``/``SignalAttempt``/``run_case_process`` bind each attempt to
  the resolved development case (global index, batch block and slot) and run
  plain, empty, sham, faults in manifest order and a clean rerun in one
  process, retaining every captured trace for offline detectors.

The replacement bytes are computed by stdlib ``signal_voice_ops``; Torch is
used only to read the original slot and write the replacement back. This
module has no CLI: campaign execution, repeats and publication belong to
#337, and nothing here has been executed against the actual Voice yet.
"""

import array
import importlib.util
import json
from pathlib import Path
import struct
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

sys.path.insert(0, str(HERE))
import mutation_worker as base  # noqa: E402

sha256 = base.sha256
tensor_bytes = base.tensor_bytes
BATCH_SIZE = base.BATCH_SIZE


def load_ops():
    spec = importlib.util.spec_from_file_location(
        "signal_voice_ops", REPO / "src/torchsynth_voice/signal_voice_ops.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["signal_voice_ops"] = module
    spec.loader.exec_module(module)
    return module


ops = load_ops()

SCHEDULE_KEYS = {
    "schema_version",
    "kind",
    "family",
    "plan_id",
    "source_binding",
    "batch_size",
    "module_mutations",
    "parameter_swaps",
    "declared_order",
    "campaign",
}
CAMPAIGN_KEYS = {
    "identity",
    "revision",
    "frozen_inventory_sha256",
    "manifest_sha256",
    "attempt",
    "entry_id",
    "disposition",
    "case",
    "repeat",
}
COMMON_KEYS = {
    "instance_id",
    "operator",
    "applicator",
    "realises",
    "slot",
    "magnitude",
    "sham",
    "ratio",
    "declared_position",
}
MODULE_KEYS = COMMON_KEYS | {"trace", "module", "occurrence", "frame", "raise"}
OPTIONAL_MODULE_KEYS = {"inputs"}
PARAMETER_KEYS = COMMON_KEYS | {"parameter", "range"}


class SignalScheduleError(ValueError):
    """A signal schedule the pinned runtime must not host."""


def _require(condition, reason):
    if not condition:
        raise SignalScheduleError(reason)


def load_manifest():
    data = (REPO / ops.MANIFEST_PATH).read_bytes()
    manifest = json.loads(data)
    ops.require_manifest_binding(manifest)
    return manifest, sha256(data)


def _magnitude_ok(spec, magnitude):
    if spec["type"] == "null":
        return magnitude is None
    if type(magnitude) is not dict or set(magnitude) != {"value", "unit"}:
        return False
    value = magnitude["value"]
    return (
        magnitude["unit"] == spec["unit"]
        and type(value) in (int, float)
        and spec.get("minimum", value) <= value <= spec.get("maximum", value)
    )


def validate_signal_schedule(schedule, document, parameter_names, case, manifest_sha256):
    """Fail closed before any render on a signal schedule outside the declared allowlist."""
    _require(type(schedule) is dict and set(schedule) == SCHEDULE_KEYS, "unsupported signal schedule")
    for kind in ("parameter_swaps", "module_mutations"):
        _require(type(schedule[kind]) is list, kind + " must be a list")
        for entry in schedule[kind]:
            _require(type(entry) is dict, "schedule entry must be an object")
            reason = ops.refused_operator(entry.get("operator"))
            _require(reason is None, "refused before rendering: " + str(reason))
    # The landed bridge validator runs first and is never bypassed.
    base.validate_schedule(schedule, document)
    _require(
        schedule["schema_version"] == 1
        and schedule["kind"] == ops.SCHEDULE_KIND
        and schedule["family"] == ops.SCHEDULE_FAMILY,
        "unsupported signal schedule",
    )
    campaign = schedule["campaign"]
    _require(type(campaign) is dict and set(campaign) == CAMPAIGN_KEYS, "campaign binding fields")
    _require(
        campaign["identity"] == ops.MANIFEST_IDENTITY
        and campaign["revision"] == ops.MANIFEST_REVISION
        and campaign["frozen_inventory_sha256"] == ops.MANIFEST_FROZEN_SHA256
        and campaign["manifest_sha256"] == manifest_sha256,
        "schedule is not bound to the committed manifest revision and digest",
    )
    _require(campaign["case"] == case, "schedule bound to a foreign case")
    _require(schedule["source_binding"]["case_id"] == case["id"], "plan bound to a foreign case")
    _require(
        type(campaign["repeat"]) is int and campaign["repeat"] >= 1, "invalid repeat id"
    )
    frames = dict((trace["name"], trace["shape"]) for trace in document["traces"])
    positions = {}
    operators = []
    for kind, keys, seam in (
        ("parameter_swaps", PARAMETER_KEYS, ops.PARAMETER_VALUE),
        ("module_mutations", MODULE_KEYS, ops.POST_MODULE),
    ):
        for entry in schedule[kind]:
            extra = set(entry) - keys
            _require(
                keys <= set(entry)
                and (extra <= OPTIONAL_MODULE_KEYS if kind == "module_mutations" else not extra),
                "schedule entry fields: " + str(entry.get("instance_id")),
            )
            instance_id = entry["instance_id"]
            positions[instance_id] = entry["declared_position"]
            operator = entry["operator"]
            _require(entry["applicator"] == operator, "applicator differs from the operator: " + operator)
            spec = ops.RUNTIME_OPERATORS[operator]
            _require(spec["seam"] == seam, "operator seam mismatch: " + operator)
            _require(entry["realises"] == spec["realises"], "realisation mismatch: " + operator)
            _require(entry["sham"] is (operator == ops.SHAM), "sham flag mismatch: " + operator)
            _require(entry["ratio"] is None, "signal entries carry no bridge ratio")
            _require(_magnitude_ok(spec["magnitude"], entry["magnitude"]),
                     "magnitude outside the operator registration: " + operator)
            _require(
                type(entry["slot"]) is int
                and 0 <= entry["slot"] < BATCH_SIZE
                and entry["slot"] == case["batch_slot"],
                "slot is not the resolved case batch slot: " + instance_id,
            )
            operators.append((entry["declared_position"], operator))
            if kind == "parameter_swaps":
                _require(
                    entry["parameter"] in parameter_names
                    and entry["parameter"] in spec["parameters"],
                    "parameter not declared for operator: " + str(entry["parameter"]),
                )
                continue
            trace = entry["trace"]
            _require(entry["raise"] is False, "signal entries never raise")
            _require(trace in spec["traces"], "trace not declared for operator: " + str(trace))
            _require(
                entry["frame"] == spec["frame"] and frames[trace] == [spec["frame"]],
                "declared frame mismatch: " + trace,
            )
            if operator == ops.MODE_SUBSTITUTE:
                _require(entry.get("inputs") == list(ops.MODE_SUBSTITUTE_INPUTS),
                         "mode substitute inputs differ from the VCA invocation's producers")
            else:
                _require("inputs" not in entry, "inputs only for the mode substitute")
    order = sorted(positions.items(), key=lambda item: item[1])
    _require(
        [position for _, position in order] == list(range(len(order))),
        "declared order mismatch",
    )
    # Compositions are checked in declared order, not list order.
    operators = [
        operator for _, operator in sorted(operators, key=lambda item: item[0])
    ]
    distinct = sorted(set(operators))
    if ops.SHAM in distinct:
        _require(operators == [ops.SHAM], "the sham control is never composed")
    if len(operators) > 1:
        _require(
            tuple(operators) in ops.COMPOSITIONS, "undeclared runtime composition: " + str(operators)
        )


class SignalMutationSession(base.MutationSession):
    """The landed scoped session with the signal applicators dispatched."""

    def __init__(self, schedule, voice, capture, torch):
        base.MutationSession.__init__(self, schedule, voice, capture, torch)
        self.consumed = []

    def _module_mutator(self, entries):
        session = self
        state = {"count": 0}

        def mutate(module, inputs, result):
            state["count"] += 1
            for entry in entries:
                if entry["occurrence"] != state["count"] - 1:
                    continue
                session._apply_family_module(entry, inputs, result)
            return None

        return mutate

    def _row_bytes(self, row):
        return tensor_bytes(row.detach(), self.torch)

    def _write_row(self, row, data):
        values = array.array("f")
        values.frombytes(data)
        with self.torch.no_grad():
            row.copy_(self.torch.tensor(values.tolist(), dtype=self.torch.float32))
        if self._row_bytes(row) != data:
            raise RuntimeError("in-place write-back does not equal the realised replacement")

    def _errored(self, entry, seam, error):
        self._record(
            entry["instance_id"],
            seam,
            "errored",
            {
                "error": type(error).__name__,
                "message": str(error),
                "operator": entry.get("operator"),
                "slot": entry["slot"],
            },
        )

    def _apply_family_module(self, entry, inputs, result):
        operator = entry.get("applicator")
        slot = entry["slot"]
        detail = {
            "operator": operator,
            "realises": entry.get("realises"),
            "trace": entry["trace"],
            "module": entry["module"],
            "occurrence": entry["occurrence"],
            "slot": slot,
            "magnitude": entry.get("magnitude"),
        }
        try:
            reason = ops.refused_operator(operator)
            if reason is not None:
                raise ops.RealisationRefused(reason)
            row = result[slot]
            original = self._row_bytes(row)
            detail["original_slot_sha256"] = sha256(original)
            if entry["sham"]:
                detail["note"] = "sham returns the original value"
                detail["replacement_slot_sha256"] = detail["original_slot_sha256"]
                self._record(entry["instance_id"], ops.POST_MODULE, "ineffective", detail)
                return
            input_bytes = None
            if operator == ops.MODE_SUBSTITUTE:
                ops.require(len(inputs) >= 2, "VCA inputs are not positional (audio_in, control_in)")
                input_bytes = [self._row_bytes(inputs[0][slot]), self._row_bytes(inputs[1][slot])]
                detail["inputs"] = list(entry["inputs"])
                detail["input_slot_sha256"] = [sha256(data) for data in input_bytes]
            replacement, realisation = ops.apply_output(
                operator, original, entry["magnitude"], entry["frame"], input_bytes
            )
            detail["realisation"] = realisation
            detail["affected_samples"] = ops.affected_samples(original, replacement)
        except ops.RealisationRefused as refusal:
            detail["reason"] = str(refusal)
            self._record(entry["instance_id"], ops.POST_MODULE, "refused", detail)
            return
        except BaseException as error:
            self._errored(entry, ops.POST_MODULE, error)
            raise
        detail["replacement_slot_sha256"] = sha256(replacement)
        if ops.numerically_equal(original, replacement):
            detail["reason"] = "replacement equals the original numerically; nothing written"
            self._record(entry["instance_id"], ops.POST_MODULE, "ineffective", detail)
            return
        try:
            self._write_row(row, replacement)
        except BaseException as error:
            self._errored(entry, ops.POST_MODULE, error)
            raise
        self.consumed.append((entry["instance_id"], entry["trace"], replacement))
        self._record(entry["instance_id"], ops.POST_MODULE, "applied", detail)

    def _apply_parameter_swap(self, swap):
        if "applicator" not in swap:
            raise SignalScheduleError("bridge parameter swaps are not hosted by the signal session")
        slot = swap["slot"]
        detail = {
            "operator": swap["operator"],
            "realises": swap["realises"],
            "parameter": swap["parameter"],
            "slot": slot,
            "magnitude": swap["magnitude"],
            "inventory_range": dict(swap["range"]),
        }
        try:
            reason = ops.refused_operator(swap["operator"])
            if reason is not None:
                raise ops.RealisationRefused(reason)
            parameter = self._parameters_by_name()[swap["parameter"]]
            saved = parameter.data
            original = self._row_bytes(saved[slot])
            detail["original_slot_sha256"] = sha256(original)
            live = parameter.parameter_range
            live_range = {
                "minimum": float(live.minimum),
                "maximum": float(live.maximum),
                "curve": float(live.curve),
                "symmetric": bool(live.symmetric),
            }
            ops.require(
                ops.same_binary32_range(live_range, swap["range"]),
                "live parameter range differs from the inventory",
            )
            normalized = struct.unpack("<f", original)[0]
            conversion = ops.convert_parameter(
                swap["operator"], normalized, live_range, swap["magnitude"]
            )
            detail["conversion"] = conversion
            expected = struct.pack("<f", conversion["replacement_normalized"])
        except ops.RealisationRefused as refusal:
            detail["reason"] = str(refusal)
            self._record(swap["instance_id"], ops.PARAMETER_VALUE, "refused", detail)
            return
        except BaseException as error:
            self._errored(swap, ops.PARAMETER_VALUE, error)
            raise
        detail["replacement_slot_sha256"] = sha256(expected)
        if expected == original:
            detail["reason"] = "converted replacement equals the original"
            self._record(swap["instance_id"], ops.PARAMETER_VALUE, "ineffective", detail)
            return
        try:
            replacement = saved.clone()
            with self.torch.no_grad():
                replacement[slot] = conversion["replacement_normalized"]
            if self._row_bytes(replacement[slot]) != expected:
                raise RuntimeError("parameter write does not equal the realised replacement")
        except BaseException as error:
            self._errored(swap, ops.PARAMETER_VALUE, error)
            raise
        self._saved_parameters[swap["instance_id"]] = (parameter, saved)
        parameter.data = replacement
        self._record(swap["instance_id"], ops.PARAMETER_VALUE, "applied", detail)


class SignalHarness(base.Harness):
    """The shared deterministic harness, bound to one resolved development case."""

    def __init__(self, capture, document, plan, torch, Voice, SynthConfig, normalize, case):
        base.Harness.__init__(self, capture, document, plan, torch, Voice, SynthConfig, normalize)
        coordinates = base.qualification.coordinates(case["global_sound_index"], BATCH_SIZE)
        _require(
            case["batch_size"] == BATCH_SIZE
            and coordinates["batch"] == case["batch_block"]
            and coordinates["slot"] == case["batch_slot"],
            "case coordinates differ from the reproducible batch coordinates",
        )
        self.coordinates = coordinates
        self.case = dict(case)


def _file_label(name):
    return name.replace(":", "__").replace(" ", "_")


class SignalAttempt(base.Attempt):
    """One bounded attempt bound to its case; retains traces and consumed replacements."""

    def run(self, output):
        harness = self.harness
        torch = harness.torch
        coordinates = harness.coordinates
        slot = coordinates["slot"]
        if self.schedule is not None:
            _require(
                self.schedule["campaign"]["case"] == harness.case
                and self.schedule["campaign"]["attempt"] == self.name,
                "attempt is not bound to its schedule case and label: " + self.name,
            )
        voice = harness.make_voice()
        rng_before = harness.rng_state()
        parameters_before = harness.capture.named_parameters(voice, torch, slot)
        noise_before = tensor_bytes(voice.noise.noise[coordinates["noise_slot"]], torch)
        session = None
        capture = None
        started = time.perf_counter()
        errored = None
        try:
            with torch.inference_mode():
                capture = harness.make_capture(voice)
                if self.schedule is None:
                    with capture:
                        audio, forward, labels = voice(coordinates["batch"])
                else:
                    session = SignalMutationSession(self.schedule, voice, capture, torch)
                    session.attach()
                    try:
                        audio, forward, labels = voice(coordinates["batch"])
                    finally:
                        if session._attached:
                            session.detach(*sys.exc_info())
        except BaseException as error:
            errored = type(error).__name__ + ": " + str(error)
        elapsed = time.perf_counter() - started
        state_restored = self._state_restored(voice, parameters_before, noise_before)
        label = _file_label(self.name)
        common = {
            "attempt": self.name,
            "case": harness.case["id"],
            "plan_id": None if self.schedule is None else self.schedule["plan_id"],
            "events": session.events if session is not None else [],
            "state_restored": state_restored,
            "render_seconds": elapsed,
        }
        if errored is not None:
            return dict(common, errored=errored)
        rng_after = harness.rng_state()
        audio_bytes = tensor_bytes(audio, torch)
        raw = output / (label + ".audio.f32le")
        raw.write_bytes(tensor_bytes(audio[slot], torch))
        retained = {}
        for name, value in capture.values.items():
            path = output / (label + ".trace." + name + ".f32le")
            path.write_bytes(tensor_bytes(value, torch))
            retained[name] = path.name
        consumed = []
        for instance_id, trace, data in (session.consumed if session is not None else []):
            path = output / (label + ".consumed." + instance_id + "." + trace + ".f32le")
            path.write_bytes(data)
            consumed.append(
                {"instance_id": instance_id, "trace": trace, "slot": slot,
                 "sha256": sha256(data), "file": path.name}
            )
        return dict(
            common,
            errored=None,
            audio_slot_sha256=sha256(tensor_bytes(audio[slot], torch)),
            batch_audio_sha256=sha256(audio_bytes),
            slot_sha256=base.slot_digests(audio_bytes),
            audio_slot_file=raw.name,
            labels_all=bool(labels.all()),
            shapes_ok=tuple(audio.shape) == (BATCH_SIZE, base.SAMPLES)
            and tuple(forward.shape) == (BATCH_SIZE, 78)
            and tuple(labels.shape) == (BATCH_SIZE,),
            capture_inventory=[dict(item) for item in capture.inventory],
            invocation_counts=dict(capture.tracker.counts),
            parameters_before_sha256=parameters_before["normalized"]["batch_sha256_by_name"],
            parameters_selected_sha256=parameters_before["normalized"]["selected_sha256_by_name"],
            noise_sha256=sha256(noise_before),
            rng_state=rng_after,
            render_drew_randoms=rng_before != rng_after,
            retained_traces=retained,
            consumed_replacements=consumed,
        )


def run_case_process(harness, schedules, output, document, parameter_names, manifest_sha256):
    """Plain, empty, sham, faults in manifest order, clean rerun; validation first."""
    names = list(schedules)
    _require(names[:2] == ["empty", "sham"], "controls must precede the faulted attempts")
    for name in names:
        validate_signal_schedule(
            schedules[name], document, parameter_names, harness.case, manifest_sha256
        )
        _require(schedules[name]["campaign"]["attempt"] == name, "schedule label mismatch: " + name)
    records = {}
    for name, schedule in [("plain", None)] + [(n, schedules[n]) for n in names] + [
        ("clean_rerun", None)
    ]:
        records[name] = SignalAttempt(name, harness, schedule).run(output)
    return records
