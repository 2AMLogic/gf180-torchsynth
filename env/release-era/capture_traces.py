"""Bounded non-perturbing trace-capture qualification worker (Python 3.9+).

Runs inside the unchanged release image for runtime profile
release-mkl-compatible-v1 (DR-0006). Imports the production capture module
``src/torchsynth_voice/trace_capture.py`` directly by file path after the
read-only source gate, exactly like the #22 prototype worker. This is the sole
old-Python helper for issue #23; it owns no registry, schema or storage.

Per preregistered case it renders the pinned batch-32 Voice uncaptured, with a
preregistered partial selection and with full capture, and requires byte
identity of final audio, named normalized/physical parameter bytes, selected
noise and RNG state across modes. It records per-mode render time and process
peak RSS, per-case capture inventories, upsampling endpoint checks, the
original normalization branch evidence, the #22 global-0 sentinel comparison,
registry and capture negative controls, and passive-cleanup injection controls.
"""

import argparse
import contextlib
import datetime
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import resource
import sys
import time
import uuid
import warnings

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

sys.path.insert(0, str(HERE))
import probe as probe_support  # noqa: E402
import qualify_repeatability as qualification  # noqa: E402
import qualify_scalar as directed_support  # noqa: E402

sha256 = probe_support.sha256
json_bytes = probe_support.json_bytes
tensor_bytes = directed_support.tensor_bytes

CAPTURE_VERSION = "trace-capture-v1"
RUNTIME_PROFILE = "release-mkl-compatible-v1"
INPUT_SHA256 = {
    "spec/reference/upstream.json": (
        "565236e22ad8b1dcd620783024a114b0960e5534b1cb26e8dcf8dafc9c96c0af"
    ),
    "spec/reference/parameter-inventory-v1.json": (
        "b360bbab2860e672a6d709296c4d88a900861fbaa30b2099d4dbd39f3399a3e6"
    ),
    "spec/reference/directed-voice-v1.json": (
        "f1c3e36eb464561faa32d190e6317e06a62f0f7b409d17b7f97f56120c569d6c"
    ),
    "spec/reference/trace-registry-v1.json": (
        "6fd72ca97edda4ea9ede968208aeed69badc2b4f642296123cb12fe1a338d34c"
    ),
}
# Preregistered bounded development set: two corpus/random draws plus the three
# original normalization branches (applied, bypassed, tied-at-boundary).
CASES = (
    {"id": "global-0", "sound_index": 0},
    {"id": "global-6", "sound_index": 6},
    {"id": "normalization:above", "sound_index": 0, "directed": "normalization:above"},
    {"id": "normalization:below", "sound_index": 0, "directed": "normalization:below"},
    {"id": "normalization:tie", "sound_index": 0, "directed": "normalization:tie"},
)
SUBSET = (
    "adsr_1.output",
    "control_upsample.vco_1_pitch",
    "mixer.peak",
    "mixer.output",
)
MODES = ("uncaptured", "partial", "full")


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


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def pinned_input_gate():
    for name, expected in INPUT_SHA256.items():
        if sha256((REPO / name).read_bytes()) != expected:
            raise ValueError("stale preregistered input: " + name)


def raw_path(output, case_id, name):
    return output / (case_id + "." + name + ".f32le")


def normalization_exercise(torch, normalize, capture):
    """Exercise the original normalize_if_clipping on synthetic shapes.

    Below/at/above one, silence, tied maximum and a late peak. The original
    division behavior and the derived diagnostic gain are checked separately;
    the derived gain is never applied to any signal here.
    """
    signals = {
        "below": [0.25, -0.5, 0.125],
        "at-boundary": [1.0, -1.0],
        "above": [2.0, -0.5],
        "silence": [0.0],
        "tied-maximum": [-1.0, 1.0],
        "late-peak": [0.5, 0.25, -3.0],
    }
    results = {}
    for name, values in signals.items():
        signal = torch.tensor([values], dtype=torch.float32)
        output = normalize(signal)
        peak = torch.max(torch.abs(signal))
        divided = bool(peak > 1)
        expected = signal / peak if divided else signal
        if tensor_bytes(output, torch) != tensor_bytes(expected, torch):
            raise ValueError("original normalization branch mismatch: " + name)
        gain = capture.derive_gain(peak.reshape(1), torch)
        expected_gain = (
            peak.reciprocal().reshape(1)
            if divided
            else torch.ones_like(peak).reshape(1)
        )
        if tensor_bytes(gain, torch) != tensor_bytes(expected_gain, torch):
            raise ValueError("derived gain mismatch: " + name)
        results[name] = {
            "division_applied": divided,
            "output_matches_original_branch": True,
            "derived_gain_matches_rule": True,
        }
    return results


def hooks_attached(voice, registry):
    return any(
        getattr(voice, name)._forward_hooks
        for name in dict.fromkeys(call[0] for call in registry.CALLS)
    )


class Harness:
    """Shared construction for one worker process; owns no capture state."""

    def __init__(self, args, registry, capture, plan, directed, names):
        self.args = args
        self.registry = registry
        self.capture = capture
        self.plan = plan
        self.directed = directed
        self.names = names

    def coordinates(self, case):
        return qualification.coordinates(case["sound_index"], 32)

    def make_voice(self, case, torch, Voice, SynthConfig):
        coordinates = self.coordinates(case)
        voice = (
            Voice(
                SynthConfig(batch_size=32, **self.plan["configuration"]),
                nebula="default",
            )
            .cpu()
            .eval()
        )
        voice.randomize(seed=coordinates["batch"])
        if "directed" in case:
            directed_support.assign_named(
                voice,
                directed_support.directed_patch(case, self.directed),
                self.names,
                torch,
                freeze=True,
            )
        return voice


def render_mode(harness, case, mode, document, torch, Voice, SynthConfig, normalize):
    """One (case, mode) execution; passive observers never mutate DSP state."""
    coordinates = harness.coordinates(case)
    slot = coordinates["slot"]
    voice = harness.make_voice(case, torch, Voice, SynthConfig)
    rng_before = tensor_bytes(torch.random.get_rng_state(), torch)
    before = harness.capture.named_parameters(voice, torch, slot)
    noise_before = tensor_bytes(voice.noise.noise[coordinates["noise_slot"]], torch)
    session = None
    started = time.perf_counter()
    with torch.inference_mode():
        if mode == "uncaptured":
            audio, forward, labels = voice(coordinates["batch"])
        else:
            session = harness.capture.TraceCapture(
                voice,
                document,
                torch,
                normalize,
                names=None if mode == "full" else SUBSET,
                slot=slot,
                batch_size=32,
            )
            with session:
                audio, forward, labels = voice(coordinates["batch"])
    elapsed = time.perf_counter() - started
    if rng_before != tensor_bytes(torch.random.get_rng_state(), torch):
        raise ValueError("render or capture consumed random values: " + mode)
    after = harness.capture.named_parameters(voice, torch, slot)
    if before != after:
        raise ValueError("capture/render mutated named parameters: " + mode)
    if noise_before != tensor_bytes(
        voice.noise.noise[coordinates["noise_slot"]], torch
    ):
        raise ValueError("selected noise buffer changed: " + mode)
    if (
        tuple(audio.shape) != (32, 176400)
        or tuple(forward.shape) != (32, 78)
        or tuple(labels.shape) != (32,)
    ):
        raise ValueError("original forward shape mismatch: " + mode)
    audio_bytes = tensor_bytes(audio, torch)
    selected_audio = tensor_bytes(audio[slot], torch)
    raw_path(harness.args.output, case["id"], mode + ".batch-audio").write_bytes(
        audio_bytes
    )
    record = {
        "mode": mode,
        "case_id": case["id"],
        "slot": slot,
        "batch": coordinates["batch"],
        "audio_sha256": sha256(selected_audio),
        "batch_audio_sha256": sha256(audio_bytes),
        "labels_all": bool(labels.all()),
        "parameter_bytes_unchanged": True,
        "noise_sha256": sha256(noise_before),
        "rng_state_unchanged": True,
        "render_seconds": elapsed,
        "process_peak_rss_kb_after": resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss,
        "batch_audio_allocation_bytes": 32 * 176400 * 4,
        "retained_capture_bytes": 0,
        "input": {
            "normalized_values": before["normalized"]["values"],
            "normalized_batch_sha256_by_name": before["normalized"][
                "batch_sha256_by_name"
            ],
            "physical_batch_sha256_by_name": before["physical"][
                "batch_sha256_by_name"
            ],
        },
    }
    if session is not None:
        inventory = []
        for item in session.inventory:
            data = tensor_bytes(session.values[item["name"]], torch)
            path = raw_path(harness.args.output, case["id"], item["name"])
            path.write_bytes(data)
            entry = dict(item)
            entry["file"] = path.name
            entry["size_bytes"] = len(data)
            inventory.append(entry)
        record["retained_capture_bytes"] = sum(
            entry["size_bytes"] for entry in inventory
        )
        record["capture_inventory"] = inventory
        record["invocation_counts"] = dict(session.tracker.counts)
    return record


def require_mode_equality(case_id, records):
    """Byte identity of traced versus untraced executions for one case."""
    reference = records["uncaptured"]
    for mode in ("partial", "full"):
        observed = records[mode]
        for key in (
            "audio_sha256",
            "batch_audio_sha256",
            "noise_sha256",
            "labels_all",
            "input",
        ):
            if observed[key] != reference[key]:
                raise ValueError(
                    "passive capture changed "
                    + key
                    + ": case "
                    + case_id
                    + " mode "
                    + mode
                )


def endpoint_checks(harness, case, records, document):
    """Selected-sound endpoints of every upsample route must survive exactly."""
    results = {}
    inventory = {
        item["name"]: item for item in records["full"]["capture_inventory"]
    }
    for control_name, upsampled_name in harness.capture.endpoint_pairs(document):
        if control_name not in inventory or upsampled_name not in inventory:
            raise ValueError("endpoint inventory missing: " + control_name)
        results[control_name] = harness.capture.check_endpoint_bytes(
            raw_path(harness.args.output, case["id"], control_name).read_bytes(),
            raw_path(harness.args.output, case["id"], upsampled_name).read_bytes(),
        )
    return results


def normalization_branch_evidence(harness, case, records, torch):
    """Original clamp branch observed on the selected sound, not reconstructed."""
    import numpy as np

    def load(name, shape):
        data = raw_path(harness.args.output, case["id"], name).read_bytes()
        return torch.from_numpy(np.frombuffer(data, dtype="<f4").copy()).reshape(shape)

    peak = load("mixer.peak", (1,))
    pre = load("mixer.pre_normalization", (176400,))
    output_bytes = raw_path(harness.args.output, case["id"], "mixer.output").read_bytes()
    divided = bool(peak > 1)
    expected = pre / peak if divided else pre
    if tensor_bytes(expected, torch) != output_bytes:
        raise ValueError("original normalization division mismatch (wrong clamp?)")
    inventory = {
        item["name"]: item for item in records["full"]["capture_inventory"]
    }
    return {
        "peak_gt_one": divided,
        "output_equals_original_branch_bytes": True,
        "pre_normalization_sha256": inventory["mixer.pre_normalization"]["sha256"],
        "peak_sha256": inventory["mixer.peak"]["sha256"],
        "output_sha256": inventory["mixer.output"]["sha256"],
    }


def sentinel_comparison(records, prototype):
    """global-0 full capture must reproduce the committed #22 prototype bytes."""
    expected_inventory = {
        item["name"]: item["sha256"] for item in prototype["capture_inventory"]
    }
    actual_inventory = {
        item["name"]: item["sha256"] for item in records["full"]["capture_inventory"]
    }
    if actual_inventory != expected_inventory:
        raise ValueError("global-0 sentinel drift: capture inventory")
    captured = prototype["executions"]["captured"]
    if records["uncaptured"]["audio_sha256"] != captured["audio_sha256"]:
        raise ValueError("global-0 sentinel drift: selected audio")
    if records["uncaptured"]["noise_sha256"] != captured["noise_sha256"]:
        raise ValueError("global-0 sentinel drift: selected noise")
    return {"status": "PASS", "compared_traces": len(expected_inventory)}


def passive_cleanup_controls(harness, document, torch, Voice, SynthConfig, normalize,
                             baseline_batch_audio_bytes):
    """Injected capture failure and a conflicting profiler must clean up."""
    registry = harness.registry
    case = CASES[0]
    coordinates = harness.coordinates(case)
    voice = harness.make_voice(case, torch, Voice, SynthConfig)
    session = harness.capture.TraceCapture(
        voice, document, torch, normalize, slot=coordinates["slot"], batch_size=32
    )
    original_add = session.add

    def failing_add(name, value):
        if name == "mixer.output":
            raise ValueError("injected capture failure")
        original_add(name, value)

    session.add = failing_add
    raised = False
    try:
        with session:
            with torch.inference_mode():
                voice(coordinates["batch"])
    except ValueError as error:
        raised = "injected capture failure" in str(error)
    if not raised:
        raise ValueError("injected capture failure was swallowed")
    if session.active or hooks_attached(voice, registry):
        raise ValueError("capture cleanup failed after injected error")
    with torch.inference_mode():
        audio, forward, labels = voice(coordinates["batch"])
    if tensor_bytes(audio, torch) != baseline_batch_audio_bytes:
        raise ValueError("state leaked after injected capture failure")

    conflicting = harness.capture.TraceCapture(
        voice, document, torch, normalize, slot=coordinates["slot"], batch_size=32
    )

    def busy_profile(*args):
        return "occupied"

    refused = False
    sys.setprofile(busy_profile)
    try:
        try:
            with conflicting:
                raise RuntimeError("unreachable body")
        except ValueError as error:
            refused = "another profiler is active" in str(error)
        except RuntimeError:
            refused = False
    finally:
        sys.setprofile(None)
    if not refused:
        raise ValueError("conflicting profiler was not refused")
    if hooks_attached(voice, registry):
        raise ValueError("refused capture left observers attached")
    return {
        "injected_failure_cleanup": "PASS",
        "post_failure_render_identical_bytes": True,
        "conflict_refused": "PASS",
    }


def harness_document():
    """Removed; kept out. See endpoint_checks(document)."""


def worker(args):
    registry, capture = load_production_modules()
    document = registry.load_registry()
    capture.requested_plan(document, SUBSET)
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
    directed = json.loads(
        (REPO / "spec/reference/directed-voice-v1.json").read_text()
    )
    inventory_document = json.loads(
        (REPO / "spec/reference/parameter-inventory-v1.json").read_text()
    )
    names = sorted(parameter["name"] for parameter in inventory_document["parameters"])
    if len(names) != 78 or len(set(names)) != 78:
        raise ValueError("inventory must contain 78 distinct names")
    prototype = json.loads(
        (REPO / "sim/reference/trace-registry-prototype.json").read_text()
    )
    args.output.mkdir(parents=True, exist_ok=True)
    harness = Harness(args, registry, capture, plan, directed, names)

    case_results = []
    baseline_batch_audio = None
    for case in CASES:
        records = {}
        for mode in MODES:
            records[mode] = render_mode(
                harness, case, mode, document, torch, Voice, SynthConfig,
                normalize_if_clipping,
            )
        require_mode_equality(case["id"], records)
        capture.validate_selected_capture(
            document, None, records["full"]["capture_inventory"], 32
        )
        capture.validate_selected_capture(
            document, list(SUBSET), records["partial"]["capture_inventory"], 32
        )
        if [
            item["name"] for item in records["partial"]["capture_inventory"]
        ] != [
            trace["name"] for trace in capture.requested_plan(document, SUBSET)
        ]:
            raise ValueError("partial selection inventory mismatch: " + case["id"])
        summary = {
            "case": case,
            "coordinates": harness.coordinates(case),
            "modes": {
                mode: {
                    key: records[mode][key]
                    for key in (
                        "audio_sha256",
                        "batch_audio_sha256",
                        "noise_sha256",
                        "labels_all",
                        "render_seconds",
                        "process_peak_rss_kb_after",
                        "retained_capture_bytes",
                        "batch_audio_allocation_bytes",
                    )
                }
                for mode in MODES
            },
            "input": records["uncaptured"]["input"],
            "full_inventory": records["full"]["capture_inventory"],
            "partial_inventory": records["partial"]["capture_inventory"],
            "full_invocation_counts": records["full"]["invocation_counts"],
            "partial_invocation_counts": records["partial"]["invocation_counts"],
            "upsampling_endpoint_checks": endpoint_checks(
                harness, case, records, document
            ),
            "normalization_branch": normalization_branch_evidence(
                harness, case, records, torch
            ),
            "passive_capture_equal_bytes": True,
        }
        if case["id"] == "global-0":
            summary["prototype_sentinel_comparison"] = sentinel_comparison(
                records, prototype
            )
            baseline_batch_audio = raw_path(
                args.output, "global-0", "uncaptured.batch-audio"
            ).read_bytes()
            summary["passive_cleanup_controls"] = passive_cleanup_controls(
                harness,
                document,
                torch,
                Voice,
                SynthConfig,
                normalize_if_clipping,
                baseline_batch_audio,
            )
        case_results.append(summary)
    measured = {
        "by_case": {
            summary["case"]["id"]: summary["modes"] for summary in case_results
        },
        "note": (
            "Wall-clock render seconds and monotonic process peak RSS after each "
            "mode; batch allocation is the original 32x176400 binary32 render "
            "and retained bytes are the selected-sound snapshots only. "
            "Projections are derived arithmetic, not measured corpus renders."
        ),
        "projection_96_cases_selected_bytes": sum(
            summary["modes"]["full"]["retained_capture_bytes"]
            for summary in case_results
        )
        * 96
        // len(case_results),
    }
    return {
        "status": "PASS",
        "schema_version": 1,
        "capture_version": CAPTURE_VERSION,
        "scope": (
            "Bounded directed/random development set, batch-32 selected sounds; "
            "production passive selective capture only"
        ),
        "configuration": plan["configuration"],
        "configuration_sha256": sha256(json_bytes(plan["configuration"])),
        "runtime_profile": RUNTIME_PROFILE,
        "runtime": runtime,
        "runtime_sha256": sha256(json_bytes(runtime)),
        "source_sha256": source,
        "source_validated_before_import": True,
        "rng_sentinel": "PASS",
        "registry_token": registry.registry_token(),
        "registry_sha256": sha256(registry.REGISTRY_PATH.read_bytes()),
        "subset": list(SUBSET),
        "cases": case_results,
        "measured_costs": measured,
        "normalization_exercise": normalization_exercise(
            torch, normalize_if_clipping, capture
        ),
        "negative_controls": capture.negative_controls(document),
        "parameter_names": names,
        "producer_sha256": {
            str(path.relative_to(REPO)): sha256(path.read_bytes())
            for path in (
                Path(__file__),
                REPO / "src/torchsynth_voice/trace_capture.py",
                REPO / "src/torchsynth_voice/trace_registry.py",
                REPO / "env/release-era/probe.py",
                REPO / "env/release-era/qualify_repeatability.py",
                REPO / "env/release-era/qualify_scalar.py",
            )
        },
        "limitations": [
            "No corpus, holdout, scalar-substitution, RTL or hardware claim",
            "Process peak RSS is monotonic; per-mode values are order-dependent",
            "Analytic range metadata is not measured activation coverage",
            "Derived mixer.gain is a diagnostic and never applied to audio",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, default=REPO / "out/trace-capture")
    parser.add_argument("--source-root", type=Path, default=Path("/opt/torchsynth"))
    args = parser.parse_args()
    if not args.worker:
        raise SystemExit(
            "worker-only helper; run it through tools/qualify_trace_capture.py"
        )
    stdout, stderr = io.StringIO(), io.StringIO()
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with (
        warnings.catch_warnings(record=True) as caught,
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        warnings.simplefilter("always")
        report = worker(args)
    report["process"] = {
        "id": os.getpid(),
        "execution_id": str(uuid.uuid4()),
        "started_utc": started,
        "argv": sys.argv,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
    }
    report["warnings"] = [str(w.message) for w in caught]
    write_json(args.output / "worker.json", report)


if __name__ == "__main__":
    main()
