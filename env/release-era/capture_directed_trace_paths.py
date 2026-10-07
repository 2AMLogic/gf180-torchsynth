"""Bounded directed trace-path capture worker (Python 3.9+), issue #286.

Runs inside the unchanged release image for runtime profile
release-mkl-compatible-v1 (DR-0006), exactly like ``capture_traces.py`` (#23),
whose Harness, source/runtime gates and pins it reuses without modification.
It executes the deterministic trace-to-case plan derived from the preregistered
``spec/reference/directed-coverage-v1.json`` (``src/torchsynth_voice/
directed_trace_paths.py``): for every planned directed case it renders the
pinned batch-32 Voice uncaptured and then with the production passive
``TraceCapture`` restricted to the traces planned for that case, requires byte
identity of final audio, named parameters, selected noise and RNG state, and
writes the planned selected-sound payloads as raw ``.f32le`` files.

The worker records execution facts only. Activation statistics are NOT trusted
from here: ``tools/qualify_directed_trace_paths.py`` rehashes the retained
payload bytes after the worker exits and derives every measurement itself.
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
import sys
import time
import uuid
import warnings

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

sys.path.insert(0, str(HERE))
import capture_traces as base  # noqa: E402  (unchanged #23 worker)

sha256 = base.sha256
tensor_bytes = base.tensor_bytes
byte_tensor_bytes = base.byte_tensor_bytes

COVERAGE_SHA256 = "b181bc4b956eba7334b24111ac6ca54188b45923019a9586ecb04c8e9726c5bc"


def load_modules():
    """Import registry, capture and plan modules by file path on Python 3.9."""
    registry, capture = base.load_production_modules()
    spec = importlib.util.spec_from_file_location(
        "directed_trace_paths", REPO / "src/torchsynth_voice/directed_trace_paths.py"
    )
    paths = importlib.util.module_from_spec(spec)
    sys.modules["directed_trace_paths"] = paths
    spec.loader.exec_module(paths)
    return registry, capture, paths


def render_case_mode(harness, case, names, output, torch, Voice, SynthConfig, normalize):
    """One (case, mode) execution; ``names`` None means uncaptured."""
    coordinates = harness.coordinates(case)
    slot = coordinates["slot"]
    voice = harness.make_voice(case, torch, Voice, SynthConfig)
    rng_before = byte_tensor_bytes(torch.random.get_rng_state(), torch)
    before = harness.capture.named_parameters(voice, torch, slot)
    noise_before = tensor_bytes(voice.noise.noise[coordinates["noise_slot"]], torch)
    session = None
    started = time.perf_counter()
    with torch.inference_mode():
        if names is None:
            audio, forward, labels = voice(coordinates["batch"])
        else:
            session = harness.capture.TraceCapture(
                voice,
                harness.document,
                torch,
                normalize,
                names=names,
                slot=slot,
                batch_size=32,
            )
            with session:
                audio, forward, labels = voice(coordinates["batch"])
    elapsed = time.perf_counter() - started
    rng_after = byte_tensor_bytes(torch.random.get_rng_state(), torch)
    after = harness.capture.named_parameters(voice, torch, slot)
    if before != after:
        raise ValueError("capture/render mutated named parameters")
    if noise_before != tensor_bytes(voice.noise.noise[coordinates["noise_slot"]], torch):
        raise ValueError("selected noise buffer changed")
    if (
        tuple(audio.shape) != (32, 176400)
        or tuple(forward.shape) != (32, 78)
        or tuple(labels.shape) != (32,)
    ):
        raise ValueError("original forward shape mismatch")
    record = {
        "audio_sha256": sha256(tensor_bytes(audio, torch)),
        "noise_sha256": sha256(noise_before),
        "labels_all": bool(labels.all()),
        "input": {
            "normalized_batch_sha256_by_name": before["normalized"]["batch_sha256_by_name"],
            "physical_batch_sha256_by_name": before["physical"]["batch_sha256_by_name"],
        },
        "rng_state": rng_after,
        "render_drew_randoms": rng_before != rng_after,
        "render_seconds": elapsed,
    }
    if session is not None:
        inventory = []
        for item in session.inventory:
            data = tensor_bytes(session.values[item["name"]], torch)
            path = output / session_file(harness, case["id"], item["name"])
            path.write_bytes(data)
            entry = dict(item)
            entry["file"] = path.name
            entry["size_bytes"] = len(data)
            inventory.append(entry)
        record["capture_inventory"] = inventory
    return record


def session_file(harness, case_id, name):
    return harness.paths.payload_filename(case_id, name)


def worker(args):
    registry, capture, paths = load_modules()
    document = registry.load_registry()
    base.pinned_input_gate()
    if sha256((REPO / "spec/reference/directed-coverage-v1.json").read_bytes()) != (
        COVERAGE_SHA256
    ):
        raise ValueError("stale preregistered input: directed-coverage-v1.json")
    coverage = json.loads((REPO / "spec/reference/directed-coverage-v1.json").read_text())
    directed = json.loads((REPO / "spec/reference/directed-voice-v1.json").read_text())
    plan_inputs = paths.build_plan(document, coverage, directed)
    qualification = base.qualification
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
    baseline = json.loads((REPO / "sim/reference/repeatability-runtime.json").read_text())
    expected_runtime = next(
        r["worker"]["runtime"] for r in baseline["records"] if r["directory"] == "release-32-1"
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
    inventory_document = json.loads(
        (REPO / "spec/reference/parameter-inventory-v1.json").read_text()
    )
    names = sorted(p["name"] for p in inventory_document["parameters"])
    if len(names) != 78 or len(set(names)) != 78:
        raise ValueError("inventory must contain 78 distinct names")
    args.output.mkdir(parents=True, exist_ok=True)
    harness = base.Harness(args, registry, capture, plan, directed, names)
    harness.document = document
    harness.paths = paths

    seen_files = set()
    case_results = []
    for planned in plan_inputs["cases"]:
        case = {"id": planned["id"], "sound_index": 0, "directed": planned["id"]}
        uncaptured = render_case_mode(
            harness, case, None, args.output, torch, Voice, SynthConfig,
            normalize_if_clipping,
        )
        selected = render_case_mode(
            harness, case, list(planned["traces"]), args.output, torch, Voice,
            SynthConfig, normalize_if_clipping,
        )
        capture.validate_selected_capture(
            document, list(planned["traces"]), selected["capture_inventory"], 32
        )
        for entry in selected["capture_inventory"]:
            if entry["file"] in seen_files:
                raise ValueError("payload file name collision: " + entry["file"])
            seen_files.add(entry["file"])
        case_results.append(
            {
                "case": case,
                "traces": list(planned["traces"]),
                "coordinates": harness.coordinates(case),
                "identity": {
                    "audio_identical": uncaptured["audio_sha256"]
                    == selected["audio_sha256"],
                    "parameters_identical": uncaptured["input"] == selected["input"],
                    "noise_identical": uncaptured["noise_sha256"]
                    == selected["noise_sha256"],
                    "rng_identical": uncaptured["rng_state"] == selected["rng_state"],
                    "labels_all": uncaptured["labels_all"] and selected["labels_all"],
                },
                "uncaptured_audio_sha256": uncaptured["audio_sha256"],
                "captured_audio_sha256": selected["audio_sha256"],
                "capture_inventory": selected["capture_inventory"],
                "render_seconds": {
                    "uncaptured": uncaptured["render_seconds"],
                    "selected": selected["render_seconds"],
                },
            }
        )
    return {
        "status": "WORKER-COMPLETE",
        "schema_version": 1,
        "receipt_version": paths.RECEIPT_VERSION,
        "runtime_profile": base.RUNTIME_PROFILE,
        "runtime": runtime,
        "runtime_sha256": sha256(base.json_bytes(runtime)),
        "source_sha256": source,
        "source_validated_before_import": True,
        "configuration": plan["configuration"],
        "registry_token": registry.registry_token(),
        "cases": case_results,
        "parameter_names": names,
        "producer_sha256": {
            str(path.relative_to(REPO)): sha256(path.read_bytes())
            for path in (
                Path(__file__),
                REPO / "env/release-era/capture_traces.py",
                REPO / "src/torchsynth_voice/directed_trace_paths.py",
                REPO / "src/torchsynth_voice/trace_capture.py",
                REPO / "src/torchsynth_voice/trace_registry.py",
                REPO / "env/release-era/probe.py",
                REPO / "env/release-era/qualify_repeatability.py",
                REPO / "env/release-era/qualify_scalar.py",
            )
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, default=REPO / "out/directed-trace-paths")
    parser.add_argument("--source-root", type=Path, default=Path("/opt/torchsynth"))
    args = parser.parse_args()
    if not args.worker:
        raise SystemExit(
            "worker-only helper; run it through tools/qualify_directed_trace_paths.py"
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
    base.write_json(args.output / "worker.json", report)


if __name__ == "__main__":
    main()
