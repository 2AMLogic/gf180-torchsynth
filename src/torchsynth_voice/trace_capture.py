"""Production passive Voice trace capture; stdlib-only, Torch injected at run time.

The sole production implementation of selective, non-perturbing capture of the
registry-named Voice seams in ``spec/reference/trace-registry-v1.json``. The
release-era worker (``env/release-era/capture_traces.py``) imports this file
directly on Python 3.9, so no Torch import and no syntax newer than 3.9 may
appear here; callers pass the Torch module and the original
``torchsynth.util.normalize_if_clipping`` explicitly.

Non-perturbation contract (spec/TRACE-CAPTURE.md):

- every observer returns ``None`` and never replaces graph code, so captured
  and uncaptured renders must stay byte-identical;
- no observer mutates inputs, outputs, parameters, buffers or RNG state and no
  observer draws random values;
- selected original values are snapshotted (cloned) before later mutation can
  alias them;
- per-render invocation bookkeeping is independent of selection: every
  registered graph call is tracked in original order with original argument
  object identity via the #22 ``CallTracker``, while only requested traces are
  retained;
- observers and the normalization-call profiler are removed after success,
  render/capture errors and partial setup failure; a conflicting active
  profiler refuses capture instead of destroying another caller's observer;
- ``mixer.gain`` is an explicitly derived binary32 diagnostic (one when the
  observed peak is at most one, otherwise its reciprocal); it is never applied
  to audio and never replaces the original peak division.
"""

from __future__ import annotations

import hashlib
import re
import sys

try:
    from . import trace_registry
except ImportError:
    # The release-era worker imports this file directly on Python 3.9.
    import trace_registry

require = trace_registry.require

ROOT = trace_registry.ROOT
PARAMETER_INVENTORY_PATH = ROOT / "spec/reference/parameter-inventory-v1.json"
PROTOTYPE_PUBLICATION_PATH = ROOT / "sim/reference/trace-registry-prototype.json"
NORMALIZATION_SEAMS = ("mixer.pre_normalization", "mixer.peak", "mixer.gain")
MULTI_OUTPUT_MODULES = ("keyboard", "mod_matrix")


def requested_plan(document, names=None):
    """Validated, graph-ordered full trace definitions for a selection."""
    selected = trace_registry.requested_names(document, names)
    by_name = {trace["name"]: trace for trace in document["traces"]}
    return [by_name[name] for name in selected]


def validate_selected_capture(document, requested, inventory, batch_size):
    """Validate a captured subset against the registry in graph order.

    ``requested`` is the name sequence originally requested (``None`` for the
    full registry set); the inventory must equal the graph-ordered resolution
    of that selection with one descriptor per requested trace.
    """
    trace_registry.validate_registry(document)
    require(
        type(batch_size) is int and batch_size > 0 and batch_size % 32 == 0,
        "capture requires supported reproducible batching",
    )
    require(
        type(inventory) is list,
        "capture inventory must be a list of descriptors",
    )
    expected_names = trace_registry.requested_names(document, requested)
    require(
        [item["name"] for item in inventory] == expected_names,
        "capture inventory mismatch: missing, extra, swapped or reordered trace",
    )
    by_name = {trace["name"]: trace for trace in document["traces"]}
    for actual, name in zip(inventory, expected_names):
        expected = by_name[name]
        require(
            actual["shape"] == expected["shape"]
            and actual["batch_shape"]
            == [batch_size if n == "B" else n for n in expected["batch_shape"]],
            "capture shape mismatch: " + name,
        )
        for key in ("dtype", "rate_hz", "boundary"):
            require(
                actual[key] == expected[key], "capture " + key + " mismatch: " + name
            )
        require(
            re.fullmatch(r"[0-9a-f]{64}", actual["sha256"]) is not None,
            "capture digest mismatch: " + name,
        )


def endpoint_pairs(document):
    """(control, upsampled) registry name pairs for every upsample route."""
    trace_registry.validate_registry(document)
    names = {trace["name"] for trace in document["traces"]}
    pairs = []
    for route in trace_registry.ROUTES:
        control = "mod_matrix." + route
        upsampled = "control_upsample." + route
        require(
            control in names and upsampled in names,
            "endpoint pair missing from registry: " + route,
        )
        pairs.append((control, upsampled))
    return pairs


def check_endpoint_bytes(control, upsampled, input_count=1764, output_count=176400):
    """Endpoint-aligned upsampling keeps first/last binary32 samples exactly.

    Truncated, padded or wrong-rate byte strings are rejected, never repaired.
    """
    require(
        len(control) == input_count * 4,
        "control endpoint check: expected "
        + str(input_count)
        + " binary32 samples, got "
        + str(len(control))
        + " bytes",
    )
    require(
        len(upsampled) == output_count * 4,
        "audio endpoint check: expected "
        + str(output_count)
        + " binary32 samples, got "
        + str(len(upsampled))
        + " bytes",
    )
    require(
        control[:4] == upsampled[:4] and control[-4:] == upsampled[-4:],
        "endpoint loss: upsampled first/last sample bytes differ from control",
    )
    return {
        "status": "PASS",
        "input_count": input_count,
        "output_count": output_count,
        "first_sample_equal": True,
        "last_sample_equal": True,
    }


def derive_gain(peak, torch):
    """Derived binary32 diagnostic only; never a writable decision seam.

    One when the observed whole-clip peak is at most one, otherwise its exact
    reciprocal. Upstream divides by the peak directly; this value is never
    applied to audio or returned as a replacement operation.
    """
    require(peak.ndim >= 1, "derived gain expects the retained peak dimension")
    return torch.where(peak > 1, peak.reciprocal(), torch.ones_like(peak))


def named_parameters(voice, torch, slot=0):
    """Name-keyed normalized/physical parameter snapshots for exact comparisons.

    Values are original resolved bytes; nothing here converts, rounds or
    substitutes a separate execution's values.
    """
    require(type(slot) is int and slot >= 0, "invalid selected slot")
    inventory_names = {
        parameter["name"]
        for parameter in trace_registry.loads(
            PARAMETER_INVENTORY_PATH.read_bytes()
        )["parameters"]
    }
    parameters = voice.get_parameters(include_frozen=True)
    values = {".".join(key): parameter for key, parameter in parameters.items()}
    require(
        set(values) == inventory_names and len(values) == len(inventory_names),
        "named parameter set mismatch",
    )
    report = {}
    for kind in ("normalized", "physical"):
        tensors = {
            name: (
                parameter.from_0to1()
                if kind == "physical"
                else parameter.detach()
            )
            for name, parameter in values.items()
        }
        report[kind] = {
            "values": {
                name: float(tensor[slot].item()) for name, tensor in tensors.items()
            },
            "selected_sha256_by_name": {
                name: hashlib.sha256(
                    tensor_bytes(tensor[slot], torch)
                ).hexdigest()
                for name, tensor in tensors.items()
            },
            "batch_sha256_by_name": {
                name: hashlib.sha256(tensor_bytes(tensor, torch)).hexdigest()
                for name, tensor in tensors.items()
            },
        }
    return report


def tensor_bytes(value, torch):
    """Original little-endian CPU binary32 bytes; never normalized or repaired."""
    require(
        value.dtype == torch.float32
        and value.device.type == "cpu"
        and sys.byteorder == "little",
        "expected original CPU little-endian float32",
    )
    require(bool(torch.isfinite(value).all()), "nonfinite observed tensor")
    return value.detach().contiguous().numpy().tobytes()


class TraceCapture:
    """Passive observers over one Voice render; every callback returns None.

    ``names`` selects a subset of registry traces (``None`` keeps all 32, an
    empty sequence keeps pure invocation bookkeeping). ``slot`` selects the
    retained batch sound. The constructor validates the selection before any
    observer is attached, so unsupported configurations fail before running.
    """

    def __init__(self, voice, document, torch, normalize, names=None, slot=0, batch_size=32):
        self.voice = voice
        self.document = document
        self.torch = torch
        self.normalize = normalize
        self.slot = slot
        self.batch_size = batch_size
        self.plan = requested_plan(document, names)
        self.requested = [trace["name"] for trace in self.plan]
        self.wanted = frozenset(self.requested)
        self.tracker = trace_registry.CallTracker()
        self.values = {}
        self.inventory = []
        self.handles = []
        self.active = False
        self.needs_profile = bool(self.wanted.intersection(NORMALIZATION_SEAMS))
        self.normalization_calls = 0
        self.normalization_frame = None
        require(
            type(slot) is int and 0 <= slot < batch_size,
            "selected slot outside supported batch",
        )

    def add(self, name, value):
        """Snapshot the selected sound of an original output; order enforced."""
        if name not in self.wanted:
            return
        position = len(self.inventory)
        require(position < len(self.plan), "extra trace: " + name)
        expected = self.plan[position]
        require(name == expected["name"], "trace observation order mismatch: " + name)
        require(
            type(self.slot) is int and -len(value) <= self.slot < len(value),
            "selected slot outside observed batch",
        )
        selected = value[self.slot].detach().clone()
        self.values[name] = selected
        self.inventory.append(
            {
                "name": name,
                "shape": list(selected.shape),
                "batch_shape": list(value.shape),
                "dtype": str(value.dtype).removeprefix("torch."),
                "rate_hz": expected["rate_hz"],
                "boundary": expected["boundary"],
                "sha256": hashlib.sha256(
                    tensor_bytes(selected, self.torch)
                ).hexdigest(),
            }
        )

    def hook(self, name):
        def observe(module, inputs, result):
            require(
                module is getattr(self.voice, name), "module identity mismatch"
            )
            outputs = result if name in MULTI_OUTPUT_MODULES else (result,)
            names = self.tracker.observe(name, inputs, outputs)
            for trace_name, value in zip(names, outputs):
                self.add(trace_name, value)

        return observe

    def profile(self, frame, event, arg):
        """Original normalize_if_clipping call input and return-frame peak."""
        if frame.f_code is not self.normalize.__code__:
            return
        if event == "call":
            require(self.normalization_calls == 0, "extra normalization invocation")
            require(
                frame.f_back.f_locals.get("self") is self.voice.mixer
                and frame.f_back.f_code is self.voice.mixer.output.__func__.__code__,
                "normalization caller boundary mismatch: wrong clamp site",
            )
            self.normalization_calls += 1
            self.normalization_frame = frame
            self.add("mixer.pre_normalization", frame.f_locals["signal"])
        elif event == "return":
            require(frame is self.normalization_frame, "normalization return mismatch")
            peak = frame.f_locals["max_sample"]
            self.add("mixer.peak", peak)
            self.add("mixer.gain", derive_gain(peak, self.torch))
            self.normalization_frame = None

    def __enter__(self):
        require(not self.active, "capture session already active")
        require(
            not self.handles and self.normalization_frame is None,
            "previous capture session was not cleaned up",
        )
        if self.needs_profile:
            require(sys.getprofile() is None, "another profiler is active")
        self.active = True
        try:
            for name in dict.fromkeys(call[0] for call in trace_registry.CALLS):
                self.handles.append(
                    getattr(self.voice, name).register_forward_hook(self.hook(name))
                )
            if self.needs_profile:
                sys.setprofile(self.profile)
        except BaseException:
            self._detach()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._detach()
        if exc_type is None:
            self.tracker.finish()
            expected_calls = 1 if self.needs_profile else 0
            require(
                self.normalization_calls == expected_calls
                and self.normalization_frame is None,
                "missing normalization observation",
            )
            validate_selected_capture(
                self.document, self.requested, self.inventory, self.batch_size
            )
        return False

    def _detach(self):
        if self.needs_profile:
            current = sys.getprofile()
            ours = (
                getattr(current, "__self__", None) is self
                and getattr(current, "__func__", None) is TraceCapture.profile
            )
            if current is None or ours:
                sys.setprofile(None)
        for handle in self.handles:
            handle.remove()
        self.handles = []
        self.active = False


def negative_controls(document):
    """Registry-level negative controls re-run through the selected validator."""
    registry_controls = trace_registry.negative_controls(document)
    full = requested_plan(document)
    inventory = [
        {
            "name": trace["name"],
            "shape": trace["shape"],
            "batch_shape": [32 if n == "B" else n for n in trace["batch_shape"]],
            "dtype": trace["dtype"],
            "rate_hz": trace["rate_hz"],
            "boundary": trace["boundary"],
            "sha256": "0" * 64,
        }
        for trace in full
    ]
    by_name = {item["name"]: dict(item) for item in inventory}
    swapped = [dict(item) for item in inventory]
    left = next(
        i for i, item in enumerate(swapped) if item["name"] == "lfo_1.post_control_vca"
    )
    right = next(
        i for i, item in enumerate(swapped) if item["name"] == "lfo_2.post_control_vca"
    )
    swapped[left]["name"], swapped[right]["name"] = (
        swapped[right]["name"],
        swapped[left]["name"],
    )
    capture_controls = {}
    cases = {
        "missing-capture": [dict(item) for item in inventory][:-1],
        "swapped-capture-names": swapped,
        "wrong-capture-rate": [
            dict(item, rate_hz=44100 if item["name"] == "adsr_1.output" else item["rate_hz"])
            for item in inventory
        ],
        "wrong-capture-shape": [
            dict(item, shape=[176400] if item["name"] == "adsr_2.output" else item["shape"])
            for item in inventory
        ],
        "wrong-capture-boundary": [
            dict(
                item,
                boundary="post-midi-clamp"
                if item["name"] == "control_upsample.vco_1_pitch"
                else item["boundary"],
            )
            for item in inventory
        ],
        "wrong-capture-clamp-site": [
            dict(
                item,
                boundary="post-conditional-normalization"
                if item["name"] == "mixer.pre_normalization"
                else item["boundary"],
            )
            for item in inventory
        ],
    }
    for name, bad in cases.items():
        try:
            validate_selected_capture(document, None, bad, 32)
        except ValueError as error:
            capture_controls[name] = {"status": "rejected", "reason": str(error)}
        else:
            raise ValueError("negative control accepted: " + name)
    require(
        by_name["mixer.output"]["rate_hz"] == 44100,
        "control invariant lost: mixer.output rate",
    )
    registry_controls.update(capture_controls)
    return registry_controls
