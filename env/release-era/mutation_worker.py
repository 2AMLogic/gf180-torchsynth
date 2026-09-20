"""Bounded actual-Voice mutation runtime worker (Python 3.9+).

Runs inside the unchanged release image for runtime profile
release-mkl-compatible-v1 (DR-0006). This is the #30 voice-runtime execution
surface for issue 30's bounded evidence: it executes host-resolved mutation
schedules (``mutation-voice-schedule-v1``) against the actual pinned Voice
while the landed #23 ``trace_capture`` stays the sole passive capture owner.
The #30 contract modules are host-side stdlib code; this worker owns only the
Torch mechanics inside the qualified runtime and records raw in-band events,
capture inventories and audio digests for host-side contract validation.

Per attempt it renders the pinned batch-32 Voice on the preregistered
global-0 case with the landed passive capture installed, applies the declared
parameter swaps and module-output replacements in the declared order after
the passive observers, records every application as an ordered event with
original/replacement slot digests, and restores all state. The baseline
attempt must reproduce the committed #22 prototype sentinel bytes; the
empty-plan and sham attempts must be byte-identical to it; every fault must
change exactly its declared blast radius; and a clean rerun after the
injected crash must be byte-identical to the baseline.
"""

import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

sys.path.insert(0, str(HERE))
import probe as probe_support  # noqa: E402
import qualify_repeatability as qualification  # noqa: E402
import qualify_scalar as directed_support  # noqa: E402

sha256 = probe_support.sha256
json_bytes = probe_support.json_bytes
tensor_bytes = directed_support.tensor_bytes

MUTATION_RUNTIME_VERSION = "mutation-runtime-v1"
RUNTIME_PROFILE = "release-mkl-compatible-v1"
CASE_ID = "global-0"
SOUND_INDEX = 0
BATCH_SIZE = 32
SAMPLES = 176400

INPUT_SHA256 = {
    "spec/reference/upstream.json": (
        "565236e22ad8b1dcd620783024a114b0960e5534b1cb26e8dcf8dafc9c96c0af"
    ),
    "spec/reference/parameter-inventory-v1.json": (
        "b360bbab2860e672a6d709296c4d88a900861fbaa30b2099d4dbd39f3399a3e6"
    ),
    "spec/reference/trace-registry-v1.json": (
        "6fd72ca97edda4ea9ede968208aeed69badc2b4f642296123cb12fe1a338d34c"
    ),
}
CAPTURE_FULL = None  # the full 32-trace registry selection


class RuntimeMutationCrash(RuntimeError):
    """The identified injected fault type raised at a voice seam."""


def load_production_modules():
    """Import trace_registry then trace_capture by file path on Python 3.9."""
    registry_spec = importlib.util.spec_from_file_location(
        "trace_registry", REPO / "src/torchsynth_voice/trace_registry.py"
    )
    registry = importlib.util.module_from_spec(registry_spec)
    sys.modules["trace_registry"] = registry
    registry_spec.loader.exec_module(registry)
    capture_spec = importlib.util.spec_from_file_location(
        "trace_capture", REPO / "src/torchsynth_voice/trace_capture.py"
    )
    capture = importlib.util.module_from_spec(capture_spec)
    sys.modules["trace_capture"] = capture
    capture_spec.loader.exec_module(capture)
    return registry, capture


def pinned_input_gate():
    for name, expected in INPUT_SHA256.items():
        if sha256((REPO / name).read_bytes()) != expected:
            raise ValueError("stale preregistered input: " + name)


def load_schedules(path):
    """Read and integrity-check the host-resolved schedule manifest."""
    manifest = json.loads((path / "manifest.json").read_text())
    schedules = {}
    for entry in manifest["schedules"]:
        data = (path / entry["file"]).read_bytes()
        if sha256(data) != entry["sha256"]:
            raise ValueError("schedule digest mismatch: " + entry["case"])
        schedules[entry["case"]] = json.loads(data)
    return schedules, manifest


def hook_seam_map(document):
    """Trace name -> (module, occurrence) for writable forward-hook outputs."""
    seam_map = {}
    for trace in document["traces"]:
        producer = trace["producer"]
        if producer["mechanism"] == "forward-hook" and producer[
            "output_index"
        ] is None:
            seam_map[trace["name"]] = (
                producer["module"],
                producer["occurrence"] - 1,
            )
    return seam_map


def validate_schedule(schedule, document):
    """Fail closed before any render on a schedule the runtime cannot host."""
    if (
        type(schedule) is not dict
        or schedule.get("schema_version") != 1
        or schedule.get("kind") != "mutation-voice-schedule-v1"
    ):
        raise ValueError("unsupported schedule")
    if schedule["batch_size"] != BATCH_SIZE:
        raise ValueError("schedule batch outside the pinned batch")
    seam_map = hook_seam_map(document)
    inventory = json.loads(
        (REPO / "spec/reference/parameter-inventory-v1.json").read_text()
    )
    inventory_names = [
        parameter["name"] for parameter in inventory["parameters"]
    ]
    positions = {}
    for kind, key in (
        ("parameter_swaps", "parameter"),
        ("module_mutations", "trace"),
    ):
        for entry in schedule[kind]:
            instance_id = entry["instance_id"]
            if instance_id in positions:
                raise ValueError("duplicate schedule instance: " + instance_id)
            positions[instance_id] = entry["declared_position"]
            if entry["slot"] < 0 or entry["slot"] >= BATCH_SIZE:
                raise ValueError("slot outside pinned batch: " + instance_id)
            if kind == "parameter_swaps":
                if entry[key] not in inventory_names:
                    raise ValueError(
                        "unknown inventory parameter: " + str(entry[key])
                    )
            else:
                if entry[key] not in seam_map:
                    raise ValueError(
                        "unknown module-output trace: " + str(entry[key])
                    )
                module, occurrence = seam_map[entry[key]]
                if entry["module"] != module or entry["occurrence"] != occurrence:
                    raise ValueError(
                        "registry association mismatch: " + entry[key]
                    )
    order = sorted(positions.items(), key=lambda item: item[1])
    if [name for name, _ in order] != schedule["declared_order"]:
        raise ValueError("declared order mismatch")
    first_module = min(
        [
            mutation["declared_position"]
            for mutation in schedule["module_mutations"]
        ]
        + [len(schedule["declared_order"])]
    )
    last_parameter = max(
        [swap["declared_position"] for swap in schedule["parameter_swaps"]]
        + [-1]
    )
    if last_parameter >= first_module:
        raise ValueError("parameter mutations must precede module mutations")


class MutationSession:
    """Scoped replacement session around one entered passive TraceCapture.

    Parameter swaps are hosted at the keyboard anchor seam (the first
    registry call): the pinned producer re-randomizes all parameters at the
    start of every render, so the swap is applied in that hook — after the
    producer's own initialization, before any later module read — by
    reference, and restored on exit. Module-output replacements are applied
    in place at the seam value (same tensor object, replaced slot data)
    inside forward hooks installed after the passive observers: the captured
    inventory keeps the original pre-mutation bytes, downstream modules and
    the returned audio consume the replacement, and the landed #22 CallTracker
    producer->consumer object identity stays intact. Every application is
    recorded as an ordered in-band event; a declared raise records the
    errored event and re-raises. All state is restored after success, render
    errors and partial setup failure.
    """

    def __init__(self, schedule, voice, capture, torch):
        self.schedule = schedule
        self.voice = voice
        self.capture = capture
        self.torch = torch
        self.events = []
        self.sham_by_instance = {}
        for kind in ("parameter_swaps", "module_mutations"):
            for entry in schedule[kind]:
                self.sham_by_instance[entry["instance_id"]] = entry["sham"]
        self._saved_parameters = {}
        self._handles = []
        self._attached = False

    def _record(self, instance_id, seam, status, detail):
        for event in self.events:
            if event["instance_id"] == instance_id:
                raise ValueError("duplicate event for instance: " + instance_id)
        self.events.append(
            {
                "instance_id": instance_id,
                "seam": seam,
                "status": status,
                "order": len(self.events),
                "sham": self.sham_by_instance[instance_id],
                "detail": detail,
            }
        )

    def _parameters_by_name(self):
        values = self.voice.get_parameters(include_frozen=True)
        return dict(
            (".".join(key), parameter) for key, parameter in values.items()
        )

    def _apply_parameter_swap(self, swap):
        parameter = self._parameters_by_name()[swap["parameter"]]
        slot = swap["slot"]
        ratio = swap["ratio"]
        saved = parameter.data
        original_bytes = tensor_bytes(saved[slot].detach(), self.torch)
        detail = {
            "parameter": swap["parameter"],
            "slot": slot,
            "ratio": ratio,
            "original_slot_sha256": sha256(original_bytes),
        }
        if swap["sham"]:
            detail["note"] = "sham returns the original value"
            detail["replacement_slot_sha256"] = detail["original_slot_sha256"]
            self._record(
                swap["instance_id"], "voice.parameter_value", "ineffective", detail
            )
            return
        replacement = saved.clone()
        with self.torch.no_grad():
            replacement[slot] = replacement[slot] * ratio
        replacement_bytes = tensor_bytes(
            replacement[slot].detach(), self.torch
        )
        detail["replacement_slot_sha256"] = sha256(replacement_bytes)
        if replacement_bytes == original_bytes:
            self._record(
                swap["instance_id"], "voice.parameter_value", "ineffective", detail
            )
            return
        self._saved_parameters[swap["instance_id"]] = (parameter, saved)
        parameter.data = replacement
        self._record(
            swap["instance_id"], "voice.parameter_value", "applied", detail
        )

    def _module_mutator(self, entries):
        session = self
        state = {"count": 0}

        def mutate(module, inputs, result):
            state["count"] += 1
            for entry in entries:
                if entry["occurrence"] != state["count"] - 1:
                    continue
                session._apply_module_entry(entry, result)
            return None

        return mutate

    def _apply_module_entry(self, entry, value):
        slot = entry["slot"]
        detail = {
            "trace": entry["trace"],
            "module": entry["module"],
            "occurrence": entry["occurrence"],
            "slot": slot,
            "ratio": entry["ratio"],
            "original_slot_sha256": sha256(
                tensor_bytes(value[slot].detach(), self.torch)
            ),
        }
        if entry["sham"]:
            detail["note"] = "sham returns the original value"
            detail["replacement_slot_sha256"] = detail["original_slot_sha256"]
            self._record(
                entry["instance_id"], "voice.post_module", "ineffective", detail
            )
            return None
        if entry["raise"]:
            self._record(
                entry["instance_id"],
                "voice.post_module",
                "errored",
                {
                    "error": "RuntimeMutationCrash",
                    "message": "injected runtime mutation crash at "
                    + entry["trace"],
                    "trace": entry["trace"],
                    "slot": slot,
                },
            )
            raise RuntimeMutationCrash(
                "injected runtime mutation crash at " + entry["trace"]
            )
        original_bytes = tensor_bytes(value[slot].detach(), self.torch)
        # The replacement is applied in place at the seam: the same tensor
        # object keeps flowing to consumers, so the landed #22 CallTracker
        # producer->consumer argument-association contract stays intact,
        # while the data the observer snapshotted before the mutation is the
        # original and everything consumed after it is the replacement.
        with self.torch.no_grad():
            value[slot].mul_(entry["ratio"])
        replacement_bytes = tensor_bytes(
            value[slot].detach(), self.torch
        )
        detail["replacement_slot_sha256"] = sha256(replacement_bytes)
        if replacement_bytes == original_bytes:
            self._record(
                entry["instance_id"], "voice.post_module", "ineffective", detail
            )
            return None
        self._record(entry["instance_id"], "voice.post_module", "applied", detail)
        return None

    def _parameter_anchor_mutator(self, swaps):
        session = self

        def mutate(module, inputs, result):
            # The pinned Voice.forward re-randomizes every parameter
            # (``randomize(seed=batch_idx)``) before the graph executes, so a
            # parameter swap is only real if it lands after that
            # re-randomization. The keyboard anchor is the first registry
            # call: when it returns, the producer's own initialization is
            # done and every later module read observes the replacement.
            for swap in swaps:
                session._apply_parameter_swap(swap)
            return None

        return mutate

    def attach(self):
        if self._attached:
            raise ValueError("mutation session already attached")
        self.capture.__enter__()
        self._attached = True
        try:
            if self.schedule["parameter_swaps"]:
                handle = self.voice.keyboard.register_forward_hook(
                    self._parameter_anchor_mutator(
                        self.schedule["parameter_swaps"]
                    )
                )
                self._handles.append(handle)
            by_module = {}
            for mutation in self.schedule["module_mutations"]:
                by_module.setdefault(mutation["module"], []).append(mutation)
            for module_name in by_module:
                entries = sorted(
                    by_module[module_name],
                    key=lambda entry: entry["declared_position"],
                )
                handle = getattr(self.voice, module_name).register_forward_hook(
                    self._module_mutator(entries)
                )
                self._handles.append(handle)
        except BaseException:
            self.detach(*sys.exc_info())
            raise
        return self

    def detach(self, exc_type, exc, traceback):
        for handle in self._handles:
            handle.remove()
        self._handles = []
        for parameter, saved in self._saved_parameters.values():
            parameter.data = saved
        self._saved_parameters = {}
        self._attached = False
        return self.capture.__exit__(exc_type, exc, traceback)


def slot_digests(batch_audio_bytes):
    width = SAMPLES * 4
    if len(batch_audio_bytes) != BATCH_SIZE * width:
        raise ValueError("unexpected batch audio size")
    return [
        sha256(batch_audio_bytes[offset : offset + width])
        for offset in range(0, len(batch_audio_bytes), width)
    ]


class Attempt:
    """One bounded execution: deterministic voice, session, raw digests."""

    def __init__(self, name, harness, schedule):
        self.name = name
        self.harness = harness
        self.schedule = schedule

    def run(self, output):
        harness = self.harness
        torch = harness.torch
        coordinates = harness.coordinates
        slot = coordinates["slot"]
        voice = harness.make_voice()
        rng_before = harness.rng_state()
        parameters_before = harness.capture.named_parameters(voice, torch, slot)
        noise_before = tensor_bytes(
            voice.noise.noise[coordinates["noise_slot"]], torch
        )
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
                    session = MutationSession(
                        self.schedule, voice, capture, torch
                    )
                    session.attach()
                    try:
                        audio, forward, labels = voice(coordinates["batch"])
                    finally:
                        if session._attached:
                            session.detach(*sys.exc_info())
        except BaseException as error:
            errored = type(error).__name__ + ": " + str(error)
        elapsed = time.perf_counter() - started
        state_restored = self._state_restored(
            voice, parameters_before, noise_before
        )
        if errored is not None:
            return {
                "attempt": self.name,
                "errored": errored,
                "events": session.events if session is not None else [],
                "state_restored": state_restored,
                "render_seconds": elapsed,
            }
        rng_after = harness.rng_state()
        audio_bytes = tensor_bytes(audio, torch)
        raw = output / (self.name + ".audio.f32le")
        raw.write_bytes(tensor_bytes(audio[slot], torch))
        record = {
            "attempt": self.name,
            "errored": None,
            "audio_slot_sha256": sha256(tensor_bytes(audio[slot], torch)),
            "batch_audio_sha256": sha256(audio_bytes),
            "slot_sha256": slot_digests(audio_bytes),
            "audio_slot_file": raw.name,
            "labels_all": bool(labels.all()),
            "shapes_ok": tuple(audio.shape) == (BATCH_SIZE, SAMPLES)
            and tuple(forward.shape) == (BATCH_SIZE, 78)
            and tuple(labels.shape) == (BATCH_SIZE,),
            "capture_inventory": [dict(item) for item in capture.inventory],
            "invocation_counts": dict(capture.tracker.counts),
            "parameters_before_sha256": parameters_before["normalized"][
                "batch_sha256_by_name"
            ],
            "parameters_selected_sha256": parameters_before["normalized"][
                "selected_sha256_by_name"
            ],
            "noise_sha256": sha256(noise_before),
            "rng_state": rng_after,
            "render_drew_randoms": rng_before != rng_after,
            "render_seconds": elapsed,
            "events": session.events if session is not None else [],
            "state_restored": state_restored,
        }
        return record

    def _state_restored(self, voice, parameters_before, noise_before):
        harness = self.harness
        parameters_after = harness.capture.named_parameters(
            voice, harness.torch, harness.coordinates["slot"]
        )
        return (
            parameters_after == parameters_before
            and noise_before
            == tensor_bytes(
                voice.noise.noise[harness.coordinates["noise_slot"]],
                harness.torch,
            )
        )


class Harness:
    """Shared deterministic construction for one worker process."""

    def __init__(
        self,
        capture,
        document,
        plan,
        torch,
        Voice,
        SynthConfig,
        normalize,
    ):
        self.capture = capture
        self.document = document
        self.plan = plan
        self.torch = torch
        self.Voice = Voice
        self.SynthConfig = SynthConfig
        self.normalize = normalize
        self.coordinates = qualification.coordinates(SOUND_INDEX, BATCH_SIZE)

    def make_voice(self):
        self.torch.manual_seed(self.coordinates["batch"])
        voice = (
            self.Voice(
                self.SynthConfig(
                    batch_size=BATCH_SIZE, **self.plan["configuration"]
                ),
                nebula="default",
            )
            .cpu()
            .eval()
        )
        voice.randomize(seed=self.coordinates["batch"])
        return voice

    def make_capture(self, voice):
        return self.capture.TraceCapture(
            voice,
            self.document,
            self.torch,
            self.normalize,
            names=CAPTURE_FULL,
            slot=self.coordinates["slot"],
            batch_size=BATCH_SIZE,
        )

    def rng_state(self):
        state = self.torch.random.get_rng_state()
        if state.device.type != "cpu" or sys.byteorder != "little":
            raise ValueError("expected original little-endian CPU byte tensor")
        return sha256(state.detach().contiguous().numpy().tobytes())


def digests_by_name(inventory):
    return {item["name"]: item["sha256"] for item in inventory}


def require_events(schedule, events):
    declared = schedule["declared_order"]
    observed = [event["instance_id"] for event in events]
    if observed != declared:
        raise ValueError(
            "event order does not match the declared order: "
            + str(observed)
            + " vs "
            + str(declared)
        )
    for event in events:
        if event["status"] not in (
            "applied",
            "ineffective",
            "errored",
            "refused",
        ):
            raise ValueError("unknown event status: " + event["status"])


def require_baseline_matches_sentinel(record, prototype):
    captured = prototype["executions"]["captured"]
    expected = {
        item["name"]: item["sha256"] for item in prototype["capture_inventory"]
    }
    actual = digests_by_name(record["capture_inventory"])
    if actual != expected:
        raise ValueError("global-0 sentinel drift: capture inventory")
    if record["audio_slot_sha256"] != captured["audio_sha256"]:
        raise ValueError("global-0 sentinel drift: selected audio")
    if record["noise_sha256"] != captured["noise_sha256"]:
        raise ValueError("global-0 sentinel drift: selected noise")


def compare_byte_identity(baseline, record, label):
    for key in (
        "batch_audio_sha256",
        "audio_slot_sha256",
        "slot_sha256",
        "noise_sha256",
        "rng_state",
        "labels_all",
        "invocation_counts",
        "capture_inventory",
        "parameters_before_sha256",
    ):
        if record[key] != baseline[key]:
            raise ValueError(
                "attempt is not byte-identical to the baseline: "
                + label
                + " ("
                + key
                + ")"
            )


def require_single_event(record, status, sham, label):
    events = record["events"]
    if len(events) != 1:
        raise ValueError(label + ": expected exactly one event")
    event = events[0]
    if (
        event["order"] != 0
        or event["status"] != status
        or event["sham"] is not sham
    ):
        raise ValueError(label + ": unexpected event " + json.dumps(event))
    detail = event["detail"]
    for key in ("original_slot_sha256", "replacement_slot_sha256"):
        if key not in detail:
            raise ValueError(label + ": event detail missing " + key)


def require_undeclared_slots_unchanged(record, plain, slot):
    for index, digest in enumerate(record["slot_sha256"]):
        if index != slot and digest != plain["slot_sha256"][index]:
            raise ValueError("undeclared batch slot changed: " + str(index))


def worker(args):
    registry, capture = load_production_modules()
    document = registry.load_registry()
    pinned_input_gate()
    plan = qualification.load_plan()
    source = qualification.source_gate(args.source_root)
    required_environment = dict(
        qualification.THREAD_ENV, **plan["profile_environment"]["release"]
    )
    if not all(os.environ.get(k) == v for k, v in required_environment.items()):
        raise ValueError("worker environment outside release-mkl-compatible-v1")
    packages = dict(
        sorted(
            (d.metadata["Name"].lower().replace("_", "-"), d.version)
            for d in importlib.metadata.distributions()
        )
    )
    sys.path.insert(0, str(args.source_root))
    import torch
    from torchsynth.config import SynthConfig, check_for_reproducibility
    from torchsynth.synth import Voice
    from torchsynth.util import normalize_if_clipping

    if Path(sys.modules["torchsynth.synth"].__file__).resolve() != (
        args.source_root / "torchsynth/synth.py"
    ).resolve():
        raise ValueError("wrong imported source")
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    runtime = qualification.runtime_record("release", plan, torch, packages)
    qualification.validate_runtime_identity(plan, "release", runtime)
    baseline = json.loads(
        (REPO / "sim/reference/repeatability-runtime.json").read_text()
    )
    expected_runtime = next(
        r["worker"]["runtime"]
        for r in baseline["records"]
        if r["directory"] == "release-32-1"
    )

    def strip_clock(text):
        return re.sub(r"^(cpu MHz|bogomips)\s*:.*\n", "", text, flags=re.MULTILINE)

    for key, value in expected_runtime.items():
        if key == "cpu":
            if strip_clock(runtime[key]) != strip_clock(value):
                raise ValueError("worker CPU identity mismatch")
        elif runtime[key] != value:
            raise ValueError("worker runtime identity mismatch: " + key)
    check_for_reproducibility()
    schedules, manifest = load_schedules(args.schedules)
    for schedule in schedules.values():
        validate_schedule(schedule, document)
    inventory_document = json.loads(
        (REPO / "spec/reference/parameter-inventory-v1.json").read_text()
    )
    parameter_names = sorted(
        parameter["name"] for parameter in inventory_document["parameters"]
    )
    if len(parameter_names) != 78:
        raise ValueError("inventory must contain 78 names")
    prototype = json.loads(
        (REPO / "sim/reference/trace-registry-prototype.json").read_text()
    )
    args.output.mkdir(parents=True, exist_ok=True)
    harness = Harness(
        capture,
        document,
        plan,
        torch,
        Voice,
        SynthConfig,
        normalize_if_clipping,
    )
    slot = harness.coordinates["slot"]

    attempts = [("plain", None)]
    for case in manifest["order"]:
        attempts.append((case, schedules[case]))
    records = {}
    for name, schedule in attempts:
        records[name] = Attempt(name, harness, schedule).run(args.output)

    plain = records["plain"]
    if plain["errored"] is not None or not plain["shapes_ok"]:
        raise ValueError("baseline attempt malformed")
    if not plain["state_restored"]:
        raise ValueError("baseline attempt did not restore state")
    require_baseline_matches_sentinel(plain, prototype)
    for name in manifest["order"]:
        record = records[name]
        require_events(schedules[name], record["events"])
        if record.get("state_restored") is not True:
            raise ValueError("attempt did not restore state: " + name)
    compare_byte_identity(plain, records["empty"], "empty-plan")
    compare_byte_identity(plain, records["sham"], "sham")
    require_single_event(records["sham"], "ineffective", True, "sham")
    require_single_event(
        records["upstream_zero"], "applied", False, "upstream fault"
    )
    require_single_event(records["final_zero"], "applied", False, "final fault")
    require_single_event(
        records["parameter_half"], "applied", False, "parameter fault"
    )

    plain_traces = digests_by_name(plain["capture_inventory"])
    upstream = digests_by_name(records["upstream_zero"]["capture_inventory"])
    final = digests_by_name(records["final_zero"]["capture_inventory"])
    parameter = digests_by_name(records["parameter_half"]["capture_inventory"])

    if upstream["control_upsample.vco_1_amp"] != plain_traces[
        "control_upsample.vco_1_amp"
    ]:
        raise ValueError("seam capture must keep the original bytes")
    if upstream["vco_1.post_vca"] == plain_traces["vco_1.post_vca"]:
        raise ValueError("downstream trace must consume the replacement")
    for sibling in (
        "control_upsample.vco_1_pitch",
        "control_upsample.vco_2_pitch",
        "control_upsample.vco_2_amp",
        "control_upsample.noise_amp",
        "noise.post_vca",
        "vco_2.post_vca",
    ):
        if upstream[sibling] != plain_traces[sibling]:
            raise ValueError("sibling trace changed: " + sibling)
    if (
        records["upstream_zero"]["slot_sha256"][slot]
        == plain["slot_sha256"][slot]
    ):
        raise ValueError("fault must change the declared slot audio")
    require_undeclared_slots_unchanged(records["upstream_zero"], plain, slot)

    if final["mixer.output"] != plain_traces["mixer.output"]:
        raise ValueError("final seam capture must keep the original bytes")
    if final["mixer.peak"] != plain_traces["mixer.peak"]:
        raise ValueError("normalization decision must stay untouched")
    if final["mixer.gain"] != plain_traces["mixer.gain"]:
        raise ValueError("derived gain must stay untouched")
    if (
        records["final_zero"]["slot_sha256"][slot]
        == plain["slot_sha256"][slot]
    ):
        raise ValueError("final replacement must reach the returned audio")
    require_undeclared_slots_unchanged(records["final_zero"], plain, slot)

    swap_detail = records["parameter_half"]["events"][0]["detail"]
    if swap_detail["original_slot_sha256"] != records[
        "parameter_half"
    ]["parameters_selected_sha256"]["adsr_1.release"]:
        raise ValueError(
            "parameter anchor did not observe the pinned post-randomization "
            "input"
        )
    if swap_detail["replacement_slot_sha256"] == swap_detail[
        "original_slot_sha256"
    ]:
        raise ValueError("parameter anchor recorded no replacement")
    if parameter["adsr_1.output"] == plain_traces["adsr_1.output"]:
        raise ValueError("parameter fault must change its own trace")
    if parameter["lfo_1.post_control_vca"] != plain_traces[
        "lfo_1.post_control_vca"
    ]:
        raise ValueError("sibling trace changed: lfo_1.post_control_vca")
    if (
        records["parameter_half"]["slot_sha256"][slot]
        == plain["slot_sha256"][slot]
    ):
        raise ValueError("parameter fault must change the declared slot audio")
    require_undeclared_slots_unchanged(records["parameter_half"], plain, slot)

    crash = records["composed_crash"]
    if crash["errored"] is None:
        raise ValueError("composed crash must abort the render")
    if not crash["errored"].startswith("RuntimeMutationCrash"):
        raise ValueError("composed crash error identity")
    if [event["status"] for event in crash["events"]] != ["applied", "errored"]:
        raise ValueError("composed crash event statuses")
    if crash["events"][1]["detail"]["error"] != "RuntimeMutationCrash":
        raise ValueError("composed crash errored event detail")

    clean_rerun = Attempt("clean_rerun", harness, None).run(args.output)
    compare_byte_identity(plain, clean_rerun, "clean rerun after crash")
    records["clean_rerun"] = clean_rerun

    for name in ["plain"] + list(manifest["order"]) + ["clean_rerun"]:
        record = records[name]
        if record.get("audio_slot_file") is not None:
            data = (args.output / record["audio_slot_file"]).read_bytes()
            if sha256(data) != record["audio_slot_sha256"]:
                raise ValueError("raw audio integrity failure: " + name)

    return {
        "status": "PASS",
        "schema_version": 1,
        "mutation_runtime_version": MUTATION_RUNTIME_VERSION,
        "scope": (
            "Bounded actual-Voice runtime fault-injection bridge on the "
            "pinned global-0 development case, batch-32 selected sound; "
            "landed #23 passive capture stays the sole capture owner"
        ),
        "configuration": plan["configuration"],
        "configuration_sha256": sha256(json_bytes(plan["configuration"])),
        "runtime_profile": RUNTIME_PROFILE,
        "runtime": runtime,
        "runtime_sha256": sha256(json_bytes(runtime)),
        "source_sha256": source,
        "source_validated_before_import": True,
        "registry_token": registry.registry_token(),
        "registry_sha256": sha256(registry.REGISTRY_PATH.read_bytes()),
        "case": {"id": CASE_ID, "sound_index": SOUND_INDEX},
        "coordinates": harness.coordinates,
        "parameter_name_count": len(parameter_names),
        "schedule_manifest_sha256": sha256(
            (args.schedules / "manifest.json").read_bytes()
        ),
        "attempts": records,
        "baseline_sentinel": "PASS",
        "producer_sha256": {
            str(path.relative_to(REPO)): sha256(path.read_bytes())
            for path in (
                Path(__file__),
                REPO / "src/torchsynth_voice/trace_capture.py",
                REPO / "src/torchsynth_voice/trace_registry.py",
                REPO / "src/torchsynth_voice/mutations.py",
                REPO / "src/torchsynth_voice/mutation_runtime.py",
                REPO / "env/release-era/probe.py",
                REPO / "env/release-era/qualify_repeatability.py",
                REPO / "env/release-era/qualify_scalar.py",
            )
        },
        "limitations": [
            "No corpus, holdout, scalar-substitution, RTL or hardware claim",
            "One pinned development case; not a family or detector qualification",
            "bridge.* operators are test-only bridge proofs, never #31/#32/#33 operators",
            "The normalization decision seam is not writable and was not mutated",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schedules", type=Path, required=True)
    parser.add_argument(
        "--source-root", type=Path, default=Path("/opt/torchsynth")
    )
    args = parser.parse_args()
    if not args.worker:
        raise SystemExit(
            "worker-only helper; run it through tools/qualify_mutations_runtime.py"
        )
    report = worker(args)
    (args.output / "worker.json").write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
