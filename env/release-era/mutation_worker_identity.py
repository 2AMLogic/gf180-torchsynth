"""Identity-family actual-Voice runtime adapter worker (#334; Python 3.9+).

Family-owned adjacent worker module for the identity slice of the runtime
family campaign (``spec/reference/runtime-family-campaign-v1.json``, DR-0012).
It does not modify the bound bridge worker ``mutation_worker.py``; it reuses
that module's release-era gates and deterministic Voice construction only
inside the pinned runtime, and it adds nothing to the seam catalog.

Every identity cell of the ratified manifest is a ``boundary_runtime`` cell at
the declared writable seam ``apparatus.producer_call``: per attempt the worker
renders a fresh pinned Voice on the resolved development case (the Voice graph
is never perturbed), then the apparatus producer call yields the resolved
request payload of that real render (``SoundIdentity`` projection of the case,
the 78 canonical normalized parameters observed after the producer's own
randomization with their pinned-equation physical values, and the selected
noise stream bytes of the pinned noise bank). Declared mutations are applied
to that payload in plan order by explicit family-owned runtime applicators
registered under the same operator ids as the #31 fixture applicators
(realization ``identity-runtime-applicators-v1``; the ``mp1-`` plan identity
binds the realization). Replacement noise streams come from the pinned
producer itself (another slot of the same bank, or the pinned ``Noise`` module
constructed with the shifted seed), never from a synthetic generator.

The passive original payload is snapshotted before any applicator runs and is
retained beside the consumed replacement payload, so the original capture is
never overwritten. Every application is an ordered in-band event
(``applied``/``ineffective``/``errored``/``refused``) carrying the source and
replacement payload digests. Wrappers are removed after success, errors and
partial setup; per-attempt state restoration and seam restoration are
recorded, never assumed.

The worker records evidence only. It never evaluates a detector and never
reports PASS: detectors run offline on the retained bytes in
``torchsynth_voice.mutations_identity_runtime`` (host-side, Python 3.11+),
which also owns plan/schedule resolution and fail-closed report validation.
Torch is imported only inside the release-era entry point, so the attempt
mechanics are importable stdlib code for synthetic adapter tests; such tests
establish adapter mechanics only and never actual-Voice runtime coverage.
"""

import argparse
import copy
import hashlib
import json
import os
from fractions import Fraction
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

ADAPTER_VERSION = "mutation-identity-runtime-v1"
REALIZATION = "identity-runtime-applicators-v1"
SCHEDULE_KIND = "mutation-identity-schedule-v1"
REPORT_KIND = "mutation-identity-worker-v1"
PAYLOAD_SCHEMA = "torchsynth-identity-runtime-payload-v1"
SEAM = "apparatus.producer_call"
EXECUTION_STATUS = "canonical-batched"
BATCH_SIZE = 32
SAMPLES = 176400
NOISE_SEED = 13
NOISE_STREAMS = 32
UPSTREAM_NOMINAL_BATCH_SIZE = 128
TRAIN_TEST_BLOCK_SIZE = 1024
EVENT_STATUSES = ("applied", "ineffective", "errored", "refused")
CONTROL_KINDS = ("plain", "empty_plan", "sham_plan", "clean_rerun")
FAULT_KINDS = ("fault", "composition")
CONVERSION_MODES = ("linear-where-curved", "curved-where-linear", "swapped-bounds")
PARAMETER_INVENTORY = "spec/reference/parameter-inventory-v1.json"
ACTUAL_VOICE_PRODUCER = "actual-voice-release-era"


class ProducerCrash(RuntimeError):
    """The identified injected fault type raised at the producer call seam."""


class RuntimeRefusal(RuntimeError):
    """A declared mutation this adapter cannot realize; refused, never applied."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode()


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def identity_projection(sound_index, render_batch_size):
    """Producer-side projection of a global sound index at a render batch size.

    This is the producer's realization; the host detector recomputes the truth
    with the pinned ``identity.SoundIdentity`` and any divergence on a control
    attempt fails closed.
    """
    _require(type(sound_index) is int and sound_index >= 0, "invalid sound index")
    _require(
        type(render_batch_size) is int
        and render_batch_size > 0
        and render_batch_size % BATCH_SIZE == 0,
        "reproducible batch_size must be a positive multiple of 32",
    )
    render_batch, render_slot = divmod(sound_index, render_batch_size)
    upstream_batch, upstream_slot = divmod(sound_index, UPSTREAM_NOMINAL_BATCH_SIZE)
    return {
        "sound_index": sound_index,
        "render_batch_size": render_batch_size,
        "render_batch_index": render_batch,
        "render_slot": render_slot,
        "upstream_nominal_batch_size": UPSTREAM_NOMINAL_BATCH_SIZE,
        "upstream_batch_index": upstream_batch,
        "upstream_slot": upstream_slot,
        "upstream_name": "synth1B1-%d-%d" % (upstream_batch, upstream_slot),
        "noise_slot": sound_index % BATCH_SIZE,
        "is_train": (sound_index // TRAIN_TEST_BLOCK_SIZE) % 10 != 9,
    }


def physical_value(row, normalized):
    """Producer realization of the pinned-equations-rational-v1 conversion.

    Same exact rational equations as ``directed.physical_value`` (which the
    host detector uses as the truth); a drift between the two fails the clean
    control instead of being absorbed.
    """
    value = Fraction(normalized)
    minimum, maximum = Fraction(row["minimum"]), Fraction(row["maximum"])
    exponent = 1.0 / row["curve"]
    if not exponent.is_integer():
        raise ValueError("unsupported noninteger reciprocal curve")
    exponent = int(exponent)
    if row["symmetric"]:
        dist = 2 * value - 1
        if row["curve"] != 1:
            value = abs(dist) ** exponent * (1 if dist > 0 else -1)
        return float(minimum + (maximum - minimum) / 2 * (value + 1))
    if row["curve"] != 1:
        value **= exponent
    return float(minimum + (maximum - minimum) * value)


def inventory_rows(root=REPO):
    """Canonical-order inventory rows (document order is the canonical order)."""
    document = json.loads((Path(root) / PARAMETER_INVENTORY).read_bytes())
    rows = {}
    for row in document["parameters"]:
        _require(row["name"] not in rows, "duplicate inventory name")
        rows[row["name"]] = row
    _require(len(rows) == 78, "inventory must contain 78 names")
    return rows


class Payload:
    """Resolved request payload: a JSON document plus its selected noise bytes."""

    def __init__(self, document, noise_samples):
        _require(type(noise_samples) is bytes, "noise samples must be bytes")
        _require(
            document["noise"]["sha256"] == sha256(noise_samples),
            "payload noise digest does not bind its samples",
        )
        self.document = document
        self.noise_samples = noise_samples

    def copy(self):
        return Payload(copy.deepcopy(self.document), self.noise_samples)

    def document_bytes(self):
        return canonical(self.document)

    def digest(self):
        # The document binds the noise bytes through noise.sha256.
        return sha256(self.document_bytes())


def make_payload(request, normalized_by_name, rows, noise_samples):
    """The apparatus producer: resolved request payload of one real render."""
    index = request["global_sound_index"]
    identity = identity_projection(index, request["batch_size"])
    _require(
        sorted(normalized_by_name) == sorted(rows),
        "observed parameter names must equal the 78 canonical names",
    )
    parameters = []
    for name, row in rows.items():
        normalized = float(normalized_by_name[name])
        parameters.append(
            {
                "name": name,
                "normalized": normalized,
                "physical": physical_value(row, normalized),
            }
        )
    document = {
        "schema": PAYLOAD_SCHEMA,
        "schema_version": 1,
        "case": dict(request),
        "sound_index": index,
        "identity_batch_size": request["batch_size"],
        "execution_status": EXECUTION_STATUS,
        "identity": identity,
        "parameters": parameters,
        "noise": {
            "seed": NOISE_SEED,
            "slot": identity["noise_slot"],
            "sample_count": len(noise_samples) // 4,
            "sha256": sha256(noise_samples),
        },
    }
    return Payload(document, noise_samples)


# --- runtime applicators (same operator ids as the #31 fixture applicators) --


def _replace_noise(payload, seed, slot, context):
    replacement = context.noise_stream(seed, slot)
    _require(type(replacement) is bytes, "producer noise stream must be bytes")
    _require(
        len(replacement) == len(payload.noise_samples),
        "producer noise stream length mismatch",
    )
    document = copy.deepcopy(payload.document)
    document["noise"] = {
        "seed": seed,
        "slot": slot,
        "sample_count": len(replacement) // 4,
        "sha256": sha256(replacement),
    }
    return Payload(document, replacement)


def _applicator_batch_shift(instance, payload, context):
    wrong_batch = instance["magnitude"]["value"]
    _require(wrong_batch % BATCH_SIZE == 0,
             "wrong render batch size must be a multiple of 32")
    document = copy.deepcopy(payload.document)
    original = dict(document["identity"])
    document["identity"] = identity_projection(document["sound_index"], wrong_batch)
    changed = sorted(
        name for name in original if original[name] != document["identity"][name]
    )
    if not changed:
        return None, {"reason": "projection at the wrong batch size is identical",
                      "wrong_render_batch_size": wrong_batch}
    return Payload(document, payload.noise_samples), {
        "wrong_render_batch_size": wrong_batch,
        "declared_render_batch_size": document["identity_batch_size"],
        "first_wrong_identity_field": changed[0],
        "changed_identity_fields": changed,
    }


def _applicator_train_flip(instance, payload, context):
    document = copy.deepcopy(payload.document)
    presented = document["identity"]
    presented["is_train"] = not presented["is_train"]
    return Payload(document, payload.noise_samples), {
        "field": "is_train",
        "original": not presented["is_train"],
        "presented": presented["is_train"],
        "train_test_block": document["sound_index"] // TRAIN_TEST_BLOCK_SIZE,
    }


def _applicator_shuffle(instance, payload, context):
    first = instance["configuration"]["first"]
    second = instance["configuration"]["second"]
    if first == second:
        return None, {"reason": "identical selection", "first": first,
                      "second": second}
    document = copy.deepcopy(payload.document)
    entries = document["parameters"]
    names = [entry["name"] for entry in entries]
    _require(first in names and second in names,
             "shuffle selection names a non-canonical parameter")
    left, right = entries[names.index(first)], entries[names.index(second)]
    for key in ("normalized", "physical"):
        left[key], right[key] = right[key], left[key]
    positions = sorted((names.index(first), names.index(second)))
    return Payload(document, payload.noise_samples), {
        "shuffled": [first, second],
        "first_wrong_canonical_parameter": names[positions[0]],
        "first_wrong_position": positions[0],
    }


def _wrong_physical(row, normalized, mode):
    minimum, maximum = row["minimum"], row["maximum"]
    if mode == "linear-where-curved":
        return minimum + (maximum - minimum) * normalized
    if mode == "curved-where-linear":
        return minimum + (maximum - minimum) * normalized ** 2
    if mode == "swapped-bounds":
        return minimum + (maximum - minimum) * (1.0 - normalized)
    raise ValueError("unknown conversion mode: " + str(mode))


def _applicator_conversion(instance, payload, context):
    mode = instance["configuration"]["mode"]
    _require(mode in CONVERSION_MODES, "unknown conversion mode: " + str(mode))
    rows = context.inventory_rows
    document = copy.deepcopy(payload.document)
    for position, entry in enumerate(document["parameters"]):
        row = rows[entry["name"]]
        applies = (
            (mode == "linear-where-curved" and row["curve"] != 1)
            or (mode == "curved-where-linear" and row["curve"] == 1)
            or (mode == "swapped-bounds" and row["minimum"] != row["maximum"])
        )
        if not applies:
            continue
        declared = entry["physical"]
        presented = _wrong_physical(row, entry["normalized"], mode)
        if presented == declared:
            continue
        entry["physical"] = presented
        return Payload(document, payload.noise_samples), {
            "mode": mode,
            "first_wrong_canonical_parameter": entry["name"],
            "first_wrong_position": position,
            "declared_physical": declared,
            "presented_physical": presented,
            "curve": row["curve"],
        }
    return None, {"reason": "no canonical parameter value changes under mode",
                  "mode": mode}


def _applicator_slot_shift(instance, payload, context):
    shift = instance["magnitude"]["value"]
    presented = payload.document["noise"]
    wrong_slot = (presented["slot"] + shift) % NOISE_STREAMS
    replacement = _replace_noise(payload, presented["seed"], wrong_slot, context)
    return replacement, {
        "original_slot": presented["slot"],
        "wrong_slot": wrong_slot,
        "seed": presented["seed"],
        "original_noise_sha256": presented["sha256"],
        "replacement_noise_sha256": replacement.document["noise"]["sha256"],
        "replacement_source": "pinned producer noise bank",
    }


def _applicator_seed_shift(instance, payload, context):
    shift = instance["magnitude"]["value"]
    presented = payload.document["noise"]
    wrong_seed = presented["seed"] + shift
    replacement = _replace_noise(payload, wrong_seed, presented["slot"], context)
    return replacement, {
        "original_seed": presented["seed"],
        "wrong_seed": wrong_seed,
        "slot": presented["slot"],
        "original_noise_sha256": presented["sha256"],
        "replacement_noise_sha256": replacement.document["noise"]["sha256"],
        "replacement_source": "pinned producer Noise module at the shifted seed",
    }


RUNTIME_APPLICATORS = {
    "identity.batch_shift": _applicator_batch_shift,
    "identity.train_test_flip": _applicator_train_flip,
    "param.positional_shuffle": _applicator_shuffle,
    "param.conversion_substitute": _applicator_conversion,
    "noise.slot_shift": _applicator_slot_shift,
    "noise.seed_shift": _applicator_seed_shift,
}
# Framework controls the session also hosts: the sham traverses dispatch and
# returns the original; the crash is the identified error-path fault.
CONTROL_OPERATORS = ("noop.sham", "crash.producer")


class IdentityInjectionSession:
    """Wrap the producer call seam for one attempt; always restore on exit.

    The original producer runs first; its result is snapshotted as the passive
    original before any applicator sees it. Unsupported instances refuse the
    whole plan at entry with a complete ``refused`` event log, before the
    producer is called.
    """

    def __init__(self, plan, targets, context):
        self.plan = plan
        self.targets = targets
        self.context = context
        self._events = []
        self._original = None
        self._active = False
        self.passive_original = None
        self.consumed = None

    def _record(self, instance, status, detail):
        for event in self._events:
            if event["instance_id"] == instance["instance_id"]:
                raise ValueError("duplicate event for instance: "
                                 + instance["instance_id"])
        detail = dict(detail)
        detail["realization"] = REALIZATION
        self._events.append(
            {
                "instance_id": instance["instance_id"],
                "seam": instance["seam"],
                "status": status,
                "order": len(self._events),
                "sham": instance["sham"],
                "detail": detail,
            }
        )

    def _unsupported(self, instance):
        if instance["seam"] != SEAM:
            return "seam is not the identity runtime seam: " + str(instance["seam"])
        operator = instance["operator"]
        if operator not in RUNTIME_APPLICATORS and operator not in CONTROL_OPERATORS:
            return "operator has no identity runtime applicator: " + str(operator)
        return None

    def __enter__(self):
        _require(not self._active, "injection session already active")
        _require(SEAM in self.targets and callable(self.targets[SEAM]),
                 "declared seam has no target: " + SEAM)
        reasons = [self._unsupported(i) for i in self.plan["mutations"]]
        if any(reasons):
            for instance, reason in zip(self.plan["mutations"], reasons):
                self._record(
                    instance,
                    "refused",
                    {
                        "reason": reason
                        or "plan refused: a sibling instance is unsupported",
                    },
                )
            raise RuntimeRefusal("; ".join(r for r in reasons if r))
        self._original = self.targets[SEAM]
        self._active = True
        try:
            self.targets[SEAM] = self._wrap(self._original)
        except BaseException:
            self._restore()
            raise
        return self

    def _wrap(self, original):
        session = self

        def dispatched(*args, **kwargs):
            result = original(*args, **kwargs)
            session.passive_original = result.copy()
            for instance in session.plan["mutations"]:
                result = session._apply(instance, result)
            session.consumed = result
            return result

        return dispatched

    def _apply(self, instance, value):
        source = value.digest()
        operator = instance["operator"]
        if operator == "noop.sham":
            self._record(instance, "ineffective", {
                "note": "sham returns the original value",
                "source_payload_sha256": source,
                "replacement_payload_sha256": source,
            })
            return value
        try:
            if operator == "crash.producer":
                raise ProducerCrash("injected producer crash at " + SEAM)
            replacement, detail = RUNTIME_APPLICATORS[operator](
                instance, value, self.context
            )
        except BaseException as error:
            self._record(instance, "errored", {
                "error": type(error).__name__,
                "message": str(error),
                "source_payload_sha256": source,
            })
            raise
        detail = dict(detail)
        detail["source_payload_sha256"] = source
        if replacement is None or replacement.digest() == source:
            detail["replacement_payload_sha256"] = source
            detail.setdefault("reason", "replacement equals original")
            self._record(instance, "ineffective", detail)
            return value
        detail["replacement_payload_sha256"] = replacement.digest()
        self._record(instance, "applied", detail)
        return replacement

    def _restore(self):
        if self._original is not None:
            self.targets[SEAM] = self._original
        self._active = False

    def __exit__(self, exc_type, exc, traceback):
        self._restore()
        return False

    @property
    def events(self):
        return [copy.deepcopy(event) for event in self._events]


# --- schedule checks and per-attempt execution ------------------------------


def validate_schedule(schedule):
    """Worker-side fail-closed structure gate before any render."""
    _require(
        type(schedule) is dict
        and schedule.get("kind") == SCHEDULE_KIND
        and schedule.get("schema_version") == 1
        and schedule.get("adapter_version") == ADAPTER_VERSION,
        "unsupported identity schedule",
    )
    request = schedule["request"]
    _require(request["batch_size"] == BATCH_SIZE, "schedule batch outside the pinned batch")
    _require(request["partition"] == "development", "only development cases may render")
    holdout = schedule["holdout"]
    index = request["global_sound_index"]
    _require(
        not holdout["first"] <= index <= holdout["last"],
        "holdout identity refused before any render",
    )
    _require(
        request["batch_block"] * request["batch_size"] + request["batch_slot"] == index,
        "case coordinates do not resolve to the declared global index",
    )
    attempts = schedule["attempts"]
    kinds = [attempt["kind"] for attempt in attempts]
    _require(kinds[:3] == ["plain", "empty_plan", "sham_plan"] and kinds[-1] == "clean_rerun",
             "attempt order must be plain, empty, sham, faults, clean_rerun")
    _require(all(kind in FAULT_KINDS for kind in kinds[3:-1]),
             "only fault/composition attempts may sit between the controls")
    names = [attempt["attempt"] for attempt in attempts]
    _require(len(set(names)) == len(names), "duplicate attempt name")
    for attempt in attempts:
        plan = attempt["plan"]
        if attempt["kind"] in ("plain", "clean_rerun"):
            _require(plan is None and attempt["plan_id"] is None,
                     "plain/clean_rerun attempts carry no plan")
            continue
        _require(type(plan) is dict and plan.get("plan_id") == attempt["plan_id"],
                 "attempt plan identity mismatch: " + attempt["attempt"])
        _require(
            [i["instance_id"] for i in plan["mutations"]] == attempt["declared_order"],
            "declared order mismatch: " + attempt["attempt"],
        )


def _retain(output, name, data):
    path = Path(output) / name
    path.write_bytes(data)
    return {"file": name, "sha256": sha256(data), "bytes": len(data)}


def _retain_payload(output, prefix, payload):
    return {
        "sha256": payload.digest(),
        "document": _retain(output, prefix + ".payload.json", payload.document_bytes()),
        "noise": _retain(output, prefix + ".noise.f32le", payload.noise_samples),
    }


def run_attempt(attempt, schedule, producer_factory, output):
    """One attempt in a fresh producer (fresh Voice); evidence only, no verdict."""
    name = attempt["attempt"]
    record = {
        "attempt": name,
        "kind": attempt["kind"],
        "cell_id": attempt["cell_id"],
        "plan_id": attempt["plan_id"],
        "errored": None,
        "events": [],
        "render": None,
        "original_payload": None,
        "presented_payload": None,
        "state_restored": False,
        "seam_restored": False,
    }
    producer = None
    session = None
    state_before = None
    targets = {}
    produce = None
    try:
        producer = producer_factory(schedule["request"])
        state_before = producer.state_token()
        render = producer.render()
        audio = render.pop("audio_slot")
        render["audio_slot"] = _retain(output, name + ".audio.f32le", audio)
        record["render"] = render
        produce = producer.produce
        targets[SEAM] = produce
        if attempt["plan"] is None:
            presented = targets[SEAM]()
            original = presented
        else:
            session = IdentityInjectionSession(attempt["plan"], targets, producer)
            with session:
                presented = session.targets[SEAM]()
            original = session.passive_original
        record["original_payload"] = _retain_payload(output, name + ".original", original)
        record["presented_payload"] = _retain_payload(output, name + ".presented", presented)
    except BaseException as error:
        record["errored"] = type(error).__name__ + ": " + str(error)
        if session is not None and session.passive_original is not None:
            record["original_payload"] = _retain_payload(
                output, name + ".original", session.passive_original
            )
    finally:
        if session is not None:
            record["events"] = session.events
        record["seam_restored"] = produce is None or targets.get(SEAM) is produce
        if producer is not None and state_before is not None:
            try:
                record["state_restored"] = producer.state_token() == state_before
            except BaseException as error:
                record["state_restored"] = False
                record["errored"] = record["errored"] or (
                    type(error).__name__ + ": " + str(error)
                )
    return record


def run_schedule(schedule, producer_factory, output):
    validate_schedule(schedule)
    Path(output).mkdir(parents=True, exist_ok=True)
    return [
        run_attempt(attempt, schedule, producer_factory, output)
        for attempt in schedule["attempts"]
    ]


def build_report(schedule, records, producer):
    """Raw worker evidence; the host evaluator alone derives verdicts."""
    return {
        "kind": REPORT_KIND,
        "schema_version": 1,
        "adapter_version": ADAPTER_VERSION,
        "realization": REALIZATION,
        "status": "EXECUTED",
        "producer": dict(producer),
        "schedule_sha256": sha256(canonical(schedule)),
        "campaign": dict(schedule["campaign"]),
        "request": dict(schedule["request"]),
        "repeat": schedule["repeat"],
        "expected_attempts": [attempt["attempt"] for attempt in schedule["attempts"]],
        "attempts": records,
        "limitations": [
            "boundary_runtime evidence at apparatus.producer_call; the Voice graph is not perturbed",
            "never counted toward graph-perturbation runtime kills (DR-0012 D1)",
            "worker evidence only: detectors and verdicts are host-side and offline",
            "no host admission, normalization, holdout, RTL or hardware claim",
        ],
    }


# --- release-era entry point (Torch imported only here) ---------------------


class ActualVoiceProducer:
    """Fresh pinned Voice for one attempt on the resolved development case."""

    kind = ACTUAL_VOICE_PRODUCER

    def __init__(self, harness, request, rows, torch, noise_class):
        self.harness = harness
        self.request = request
        self.inventory_rows = rows
        self.torch = torch
        self.noise_class = noise_class
        self.voice = harness.make_voice()

    def _tensor_bytes(self, tensor):
        import qualify_scalar

        return qualify_scalar.tensor_bytes(tensor, self.torch)

    def state_token(self):
        coordinates = self.harness.coordinates
        parameters = self.harness.capture.named_parameters(
            self.voice, self.torch, coordinates["slot"]
        )
        noise = self._tensor_bytes(self.voice.noise.noise[coordinates["noise_slot"]])
        return sha256(canonical(parameters["normalized"]["batch_sha256_by_name"])
                      + noise)

    def render(self):
        import mutation_worker as base

        torch = self.torch
        coordinates = self.harness.coordinates
        slot = coordinates["slot"]
        with torch.inference_mode():
            capture = self.harness.make_capture(self.voice)
            with capture:
                audio, forward, labels = self.voice(coordinates["batch"])
        audio_bytes = self._tensor_bytes(audio)
        return {
            "audio_slot": self._tensor_bytes(audio[slot]),
            "batch_audio_sha256": sha256(audio_bytes),
            "slot_sha256": base.slot_digests(audio_bytes),
            "capture_inventory": [dict(item) for item in capture.inventory],
            "invocation_counts": dict(capture.tracker.counts),
            "rng_state": self.harness.rng_state(),
            "shapes_ok": tuple(audio.shape) == (BATCH_SIZE, SAMPLES)
            and tuple(forward.shape) == (BATCH_SIZE, 78)
            and tuple(labels.shape) == (BATCH_SIZE,),
            "labels_all": bool(labels.all()),
        }

    def produce(self):
        coordinates = self.harness.coordinates
        parameters = self.harness.capture.named_parameters(
            self.voice, self.torch, coordinates["slot"]
        )
        noise = self._tensor_bytes(self.voice.noise.noise[coordinates["noise_slot"]])
        return make_payload(
            self.request, parameters["normalized"]["values"], self.inventory_rows, noise
        )

    def noise_stream(self, seed, slot):
        _require(0 <= slot < NOISE_STREAMS, "noise slot outside [0, 32)")
        if seed == NOISE_SEED:
            return self._tensor_bytes(self.voice.noise.noise[slot])
        bank = self.noise_class(self.voice.noise.synthconfig, seed=seed)
        return self._tensor_bytes(bank.noise[slot])


def worker(args):  # pragma: no cover - release-era image only
    sys.path.insert(0, str(HERE))
    import mutation_worker as base
    import qualify_repeatability as qualification

    schedule_bytes = args.schedule.read_bytes()
    schedule = json.loads(schedule_bytes)
    validate_schedule(schedule)
    registry, capture = base.load_production_modules()
    document = registry.load_registry()
    base.pinned_input_gate()
    plan = qualification.load_plan()
    source = qualification.source_gate(args.source_root)
    required_environment = dict(
        qualification.THREAD_ENV, **plan["profile_environment"]["release"]
    )
    if not all(os.environ.get(k) == v for k, v in required_environment.items()):
        raise ValueError("worker environment outside release-mkl-compatible-v1")
    import importlib.metadata

    packages = dict(
        sorted(
            (d.metadata["Name"].lower().replace("_", "-"), d.version)
            for d in importlib.metadata.distributions()
        )
    )
    sys.path.insert(0, str(args.source_root))
    import torch
    from torchsynth.config import SynthConfig, check_for_reproducibility
    from torchsynth.module import Noise
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
    baseline = json.loads((REPO / "sim/reference/repeatability-runtime.json").read_text())
    expected_runtime = next(
        r["worker"]["runtime"] for r in baseline["records"]
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
    rows = inventory_rows()
    harness = base.Harness(
        capture, document, plan, torch, Voice, SynthConfig, normalize_if_clipping
    )
    request = schedule["request"]
    harness.coordinates = qualification.coordinates(
        request["global_sound_index"], request["batch_size"]
    )

    def factory(case_request):
        return ActualVoiceProducer(harness, case_request, rows, torch, Noise)

    records = run_schedule(schedule, factory, args.output)
    if request["global_sound_index"] == base.SOUND_INDEX:
        plain = next(r for r in records if r["kind"] == "plain")
        if plain["errored"] is not None:
            raise ValueError("plain attempt errored")
        prototype = json.loads(
            (REPO / "sim/reference/trace-registry-prototype.json").read_text()
        )
        base.require_baseline_matches_sentinel(
            {
                "capture_inventory": plain["render"]["capture_inventory"],
                "audio_slot_sha256": plain["render"]["audio_slot"]["sha256"],
                "noise_sha256": plain["original_payload"]["noise"]["sha256"],
            },
            prototype,
        )
    producer = {
        "kind": ACTUAL_VOICE_PRODUCER,
        "runtime_profile": base.RUNTIME_PROFILE,
        "runtime_sha256": sha256(canonical(runtime)),
        "source_sha256": source,
        "registry_token": registry.registry_token(),
        "registry_sha256": sha256(registry.REGISTRY_PATH.read_bytes()),
        "schedule_file_sha256": sha256(schedule_bytes),
        "producer_sha256": {
            str(path.relative_to(REPO)): sha256(path.read_bytes())
            for path in (Path(__file__), HERE / "mutation_worker.py")
        },
    }
    return build_report(schedule, records, producer)


def main():  # pragma: no cover - release-era image only
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path("/opt/torchsynth"))
    args = parser.parse_args()
    if not args.worker:
        raise SystemExit(
            "worker-only helper; the campaign launcher (#337) runs it in the release image"
        )
    report = worker(args)
    (args.output / "identity-worker.json").write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
