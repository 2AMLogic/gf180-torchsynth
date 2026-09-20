"""Issue #40 control-path reference capture worker (Python 3.9+).

Runs inside the unchanged release image for runtime profile
release-mkl-compatible-v1 (DR-0006). Imports the landed production capture
module ``src/torchsynth_voice/trace_capture.py`` by file path after the
read-only source gate, exactly like the #23 worker. Renders the
preregistered fixture set for the independent float control path:

- every directed case of ``spec/reference/directed-voice-v1.json``
  (392 patches; batch-32 reproducible, patch frozen at slot 0), and
- the first clean development corpus, global indices 0-95
  (``spec/DEVELOPMENT-CORPUS.md``), each rendered in its own reproducible
  batch with the global slot selected.

Per case it records the observed normalized and physical parameter maps
(``physical.parameters`` is the measured-observation seam: the observed
runtime conversion, never a re-derivation), the selected noise stream
digest and bytes, and the 22 declared control-path traces selected from the
landed trace registry. Capture stays strictly passive: observers return
``None``; graph code is never wrapped or replaced. Raw trace bytes and a
per-case JSON record are written under the output directory; the aggregate
manifest is written only after every case succeeds.
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
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

CAPTURE_VERSION = "control-path-capture-v1"
RUNTIME_PROFILE = "release-mkl-compatible-v1"
SOURCE_ROOT_DEFAULT = Path("/opt/torchsynth")
REGISTRY_PATH = REPO / "spec/reference/trace-registry-v1.json"
DIRECTED_PATH = REPO / "spec/reference/directed-voice-v1.json"
INVENTORY_PATH = REPO / "spec/reference/parameter-inventory-v1.json"

# The declared control-path selection: checkpoint orders 1-13 plus the four
# remaining shared control_upsample instances (orders 15, 17, 19, 22).
CONTROL_TRACES = (
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
)

DEVELOPMENT_INDICES = tuple(range(96))


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
    sys.modules["capture"] = capture
    capture_spec.loader.exec_module(capture)
    return registry, capture


def runtime_identity(torch):
    return {
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "runtime_profile": RUNTIME_PROFILE,
    }


def source_hashes():
    manifest = json.loads((REPO / "spec/reference/upstream.json").read_text())
    return {name: sha256((REPO / name).read_bytes()) for name in manifest["files"]}


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def trace_rate(name):
    if name.startswith("control_upsample."):
        return 44100
    return 441


def case_dir(output, case_id):
    safe = case_id.replace(":", "_")
    return output / "cases" / safe


def save_case(output, case_id, torch, values, noise_bytes, record):
    directory = case_dir(output, case_id)
    directory.mkdir(parents=True, exist_ok=True)
    traces = {}
    for name, tensor in values.items():
        payload = tensor_bytes(tensor, torch)
        (directory / (name + ".f32le")).write_bytes(payload)
        traces[name] = {
            "file": name + ".f32le",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "rate_hz": trace_rate(name),
        }
    record["traces"] = traces
    (directory / "input.noise.f32le").write_bytes(noise_bytes)
    record["noise"] = {
        "file": "input.noise.f32le",
        "sha256": hashlib.sha256(noise_bytes).hexdigest(),
        "size_bytes": len(noise_bytes),
    }
    write_json(directory / "case.json", record)


def render_cases(
    args, torch, SynthConfig, Voice, normalize, registry_document, capture_module
):
    directed = json.loads(DIRECTED_PATH.read_text())
    inventory_names = sorted(
        row["name"] for row in json.loads(INVENTORY_PATH.read_text())["parameters"]
    )
    if len(inventory_names) != 78:
        raise ValueError("inventory must contain 78 names")

    cases = []
    for entry in directed["cases"]:
        cases.append(
            {"kind": "directed", "id": "directed:" + entry["id"], "entry": entry}
        )
    for index in DEVELOPMENT_INDICES:
        cases.append(
            {
                "kind": "development",
                "id": "development:global-%d" % index,
                "sound_index": index,
            }
        )
    if args.smoke:
        cases = [cases[0], cases[len(directed["cases"])], cases[-1]]

    started = time.time()
    results = []
    for position, case in enumerate(cases):
        voice = (
            Voice(
                SynthConfig(batch_size=32, reproducible=True, buffer_size_seconds=4.0),
                nebula="default",
            )
            .cpu()
            .eval()
        )
        with torch.no_grad():
            if case["kind"] == "directed":
                voice.randomize(seed=0)
                patch = directed_support.directed_patch(
                    {"directed": case["entry"]["id"]}, directed
                )
                directed_support.assign_named(
                    voice, patch, inventory_names, torch, freeze=True
                )
                slot = 0
                batch_index = 0
            else:
                coordinates = probe_support.identity(case["sound_index"])
                slot = coordinates["render_slot"]
                batch_index = coordinates["render_batch_index"]
                voice.randomize(seed=batch_index)
            normalized = directed_support.named_values(voice, slot)
            physical = directed_support.named_values(voice, slot, physical=True)
            if sorted(normalized) != inventory_names:
                raise ValueError("normalized map must cover the 78 canonical names")
            if sorted(physical) != inventory_names:
                raise ValueError("physical map must cover the 78 canonical names")
            noise_bytes = tensor_bytes(voice.noise.noise[slot], torch)
            capture = capture_module.TraceCapture(
                voice,
                registry_document,
                torch,
                normalize,
                names=list(CONTROL_TRACES),
                slot=slot,
                batch_size=32,
            )
            with capture:
                voice(batch_index)
        record = {
            "capture_version": CAPTURE_VERSION,
            "case_id": case["id"],
            "kind": case["kind"],
            "sound_index": case.get("sound_index", 0),
            "directed_case": case["entry"]["id"]
            if case["kind"] == "directed"
            else None,
            "batch_index": batch_index,
            "slot": slot,
            "is_train": True,
            "noise_slot": slot,
            "normalized_by_name": normalized,
            "physical_by_name": physical,
            "normalized_sha256": sha256(json_bytes(sorted(normalized.items()))),
            "physical_sha256": sha256(json_bytes(sorted(physical.items()))),
            "noise_declared": {"seed": 13, "slot": slot, "sample_count": 176400},
        }
        save_case(args.output, case["id"], torch, capture.values, noise_bytes, record)
        record["expected_traces"] = list(CONTROL_TRACES)
        results.append(record)
        print("captured %d/%d %s" % (position + 1, len(cases), case["id"]), flush=True)
    return results, time.time() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=REPO / "out/control-path-capture"
    )
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT_DEFAULT)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if not args.worker:
        parser.error("worker-only helper; drive it through a container run")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("output directory must not exist or be empty")

    registry, capture = load_production_modules()
    document = registry.load_registry()
    selected = registry.requested_names(document, list(CONTROL_TRACES))
    if selected != list(CONTROL_TRACES):
        raise ValueError("control-path selection must match registry order")

    hashes = qualification.source_gate(args.source_root)
    if any(
        name == "torchsynth" or name.startswith("torchsynth.") for name in sys.modules
    ):
        raise ValueError("TorchSynth imported before source gate")
    sys.path.insert(0, str(args.source_root))
    import torch
    from torchsynth.config import SynthConfig
    from torchsynth.synth import Voice
    from torchsynth import util

    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)

    args.output.mkdir(parents=True, exist_ok=True)
    results, elapsed = render_cases(
        args, torch, SynthConfig, Voice, util.normalize_if_clipping, document, capture
    )
    manifest = {
        "capture_version": CAPTURE_VERSION,
        "runtime_profile": RUNTIME_PROFILE,
        "runtime": runtime_identity(torch),
        "source_sha256": hashes,
        "source_root": str(args.source_root),
        "registry_sha256": sha256(REGISTRY_PATH.read_bytes()),
        "directed_sha256": sha256(DIRECTED_PATH.read_bytes()),
        "inventory_sha256": sha256(INVENTORY_PATH.read_bytes()),
        "selection": "smoke" if args.smoke else "preregistered-issue-40",
        "control_traces": list(CONTROL_TRACES),
        "development_indices": list(DEVELOPMENT_INDICES),
        "cases": [
            {
                key: result[key]
                for key in (
                    "case_id",
                    "kind",
                    "sound_index",
                    "directed_case",
                    "batch_index",
                    "slot",
                    "is_train",
                    "noise_slot",
                    "normalized_sha256",
                    "physical_sha256",
                    "noise",
                    "expected_traces",
                )
            }
            for result in results
        ],
        "case_count": len(results),
        "elapsed_seconds": elapsed,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    write_json(args.output / "manifest.json", manifest)
    print("wrote manifest with %d cases in %.1fs" % (len(results), elapsed), flush=True)


if __name__ == "__main__":
    main()
