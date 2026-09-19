"""One global-0 capture experiment, not a corpus renderer (Python 3.9+)."""

import argparse
import contextlib
import datetime
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import uuid
import warnings

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "trace_registry", ROOT / "src/torchsynth_voice/trace_registry.py"
)
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)
require = registry.require


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def tensor_bytes(value, torch):
    require(
        value.dtype == torch.float32
        and value.device.type == "cpu"
        and sys.byteorder == "little",
        "expected original CPU little-endian float32",
    )
    require(bool(torch.isfinite(value).all()), "nonfinite observed tensor")
    return value.detach().contiguous().numpy().tobytes()


class PassiveCapture:
    """Observe original graph calls; every callback returns None and removes itself."""

    def __init__(self, voice, document, torch, normalize):
        self.voice, self.document, self.torch = voice, document, torch
        self.normalize = normalize
        self.tracker = registry.CallTracker()
        self.values, self.inventory, self.handles = {}, [], []
        self.normalization_calls = 0
        self.normalization_frame = None

    def add(self, name, value):
        position = len(self.inventory)
        require(position < len(self.document["traces"]), "extra trace: " + name)
        expected = self.document["traces"][position]
        require(name == expected["name"], "trace observation order mismatch: " + name)
        # Snapshot only the selected sound. Retain original objects separately for
        # call association; no callback changes inputs, outputs, parameters or RNG.
        selected = value[0].detach().clone()
        self.values[name] = selected
        self.inventory.append(
            {
                "name": name,
                "shape": list(selected.shape),
                "batch_shape": list(value.shape),
                "dtype": str(value.dtype).removeprefix("torch."),
                "rate_hz": expected["rate_hz"],
                "boundary": expected["boundary"],
                "sha256": digest(tensor_bytes(selected, self.torch)),
            }
        )

    def hook(self, name):
        def observe(module, inputs, result):
            require(module is getattr(self.voice, name), "module identity mismatch")
            outputs = result if name in ("keyboard", "mod_matrix") else (result,)
            names = self.tracker.observe(name, inputs, outputs)
            for trace_name, value in zip(names, outputs):
                self.add(trace_name, value)

        return observe

    def profile(self, frame, event, arg):
        if frame.f_code is not self.normalize.__code__:
            return
        if event == "call":
            require(self.normalization_calls == 0, "extra normalization invocation")
            require(
                frame.f_back.f_locals.get("self") is self.voice.mixer
                and frame.f_back.f_code is self.voice.mixer.output.__func__.__code__,
                "normalization caller boundary mismatch",
            )
            self.normalization_calls += 1
            self.normalization_frame = frame
            self.add("mixer.pre_normalization", frame.f_locals["signal"])
        elif event == "return":
            require(frame is self.normalization_frame, "normalization return mismatch")
            peak = frame.f_locals["max_sample"]
            self.add("mixer.peak", peak)
            # Explicit diagnostic only: never substitute this reciprocal for DSP.
            gain = self.torch.where(
                peak > 1, peak.reciprocal(), self.torch.ones_like(peak)
            )
            self.add("mixer.gain", gain)
            self.normalization_frame = None

    def __enter__(self):
        require(sys.getprofile() is None, "another profiler is active")
        try:
            for name in dict.fromkeys(call[0] for call in registry.CALLS):
                self.handles.append(
                    getattr(self.voice, name).register_forward_hook(self.hook(name))
                )
            sys.setprofile(self.profile)
        except BaseException:
            for handle in self.handles:
                handle.remove()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        sys.setprofile(None)
        for handle in self.handles:
            handle.remove()
        if exc_type is None:
            self.tracker.finish()
            require(
                self.normalization_calls == 1 and self.normalization_frame is None,
                "missing normalization observation",
            )
            registry.validate_capture(self.document, self.inventory, 32)


def named(voice, torch):
    parameters = voice.get_parameters(include_frozen=True)
    expected = {
        p["name"]
        for p in json.loads(
            (ROOT / "spec/reference/parameter-inventory-v1.json").read_text()
        )["parameters"]
    }
    values = {".".join(k): p for k, p in parameters.items()}
    require(
        set(values) == expected and len(values) == 78, "named parameter set mismatch"
    )
    report, payload = {}, {}
    for kind in ("normalized", "physical"):
        tensors = {
            n: values[n].from_0to1() if kind == "physical" else values[n].detach()
            for n in sorted(values)
        }
        full = {n: tensor_bytes(t, torch) for n, t in tensors.items()}
        selected = {n: tensor_bytes(t[0], torch) for n, t in tensors.items()}
        report[kind] = {
            "values": {n: float(t[0]) for n, t in tensors.items()},
            "bytes_hex_by_name": {n: b.hex() for n, b in selected.items()},
            "selected_sha256": digest(b"".join(selected.values())),
            "batch_sha256_by_name": {n: digest(b) for n, b in full.items()},
        }
        payload[kind] = full
    return report, payload


def worker(args):
    # Reuse the existing *read-only* source/runtime gates, not their probe capture.
    sys.path.insert(0, str(ROOT / "env/release-era"))
    import qualify_repeatability as qualification

    document = registry.load_registry()
    plan = qualification.load_plan()
    source = qualification.source_gate(args.source_root)
    required_environment = dict(
        qualification.THREAD_ENV, **plan["profile_environment"]["release"]
    )
    require(
        all(os.environ.get(k) == v for k, v in required_environment.items()),
        "worker environment outside release-mkl-compatible-v1",
    )
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

    require(
        Path(sys.modules["torchsynth.synth"].__file__).resolve()
        == (args.source_root / "torchsynth/synth.py").resolve(),
        "wrong imported source",
    )
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    runtime = qualification.runtime_record("release", plan, torch, packages)
    qualification.validate_runtime_identity(plan, "release", runtime)
    baseline = json.loads(
        (ROOT / "sim/reference/repeatability-runtime.json").read_text()
    )
    expected_runtime = next(
        r["worker"]["runtime"]
        for r in baseline["records"]
        if r["directory"] == "release-32-1"
    )
    for key, value in expected_runtime.items():
        if key == "cpu":
            # These two measured clock values vary with scheduling; preserve the
            # full observed text while comparing every other CPU feature field.
            def strip_clock(text):
                return re.sub(
                    r"^(cpu MHz|bogomips)\s*:.*\n", "", text, flags=re.MULTILINE
                )

            require(
                strip_clock(runtime[key]) == strip_clock(value),
                "worker CPU identity mismatch",
            )
        else:
            require(runtime[key] == value, "worker runtime identity mismatch: " + key)
    check_for_reproducibility()
    args.output.mkdir(parents=True, exist_ok=True)
    sides, raw = {}, {}
    capture = None
    for side in ("uncaptured", "captured"):
        voice = (
            Voice(SynthConfig(batch_size=32, **plan["configuration"]), nebula="default")
            .cpu()
            .eval()
        )
        voice.randomize(0)
        before, before_bytes = named(voice, torch)
        noise_before = tensor_bytes(voice.noise.noise, torch)
        with torch.inference_mode():
            if side == "captured":
                capture = PassiveCapture(voice, document, torch, normalize_if_clipping)
                with capture:
                    audio, forward, labels = voice(0)
            else:
                audio, forward, labels = voice(0)
        after, after_bytes = named(voice, torch)
        require(
            before_bytes == after_bytes,
            "capture/render mutated named parameters: " + side,
        )
        require(
            noise_before == tensor_bytes(voice.noise.noise, torch),
            "noise buffer changed",
        )
        require(
            tuple(audio.shape) == (32, 176400) and tuple(forward.shape) == (32, 78),
            "original forward shape mismatch",
        )
        require(
            tuple(labels.shape) == (32,) and bool(labels.all()),
            "global-0 label mismatch",
        )
        audio_bytes = tensor_bytes(audio, torch)
        selected = tensor_bytes(audio[0], torch)
        noise = tensor_bytes(voice.noise.noise[0], torch)
        (args.output / (side + ".audio.f32le")).write_bytes(audio_bytes)
        sides[side] = {
            "before": before,
            "after": after,
            "audio_sha256": digest(selected),
            "batch_audio_sha256": digest(audio_bytes),
            "noise_sha256": digest(noise),
            "parameter_bytes_unchanged": True,
        }
        raw[side] = (audio_bytes, after_bytes, noise)
    require(
        raw["captured"] == raw["uncaptured"],
        "passive capture changed original output or inputs",
    )
    require(
        tensor_bytes(capture.values["mixer.output"], torch) == selected,
        "final Voice audio differs from mixer observation",
    )
    require(
        tensor_bytes(capture.values["noise.raw"], torch) == noise,
        "noise checkpoint mismatch",
    )
    endpoints = {}
    for route in registry.ROUTES:
        control = capture.values["mod_matrix." + route]
        upsampled = capture.values["control_upsample." + route]
        require(
            tensor_bytes(control[[0, -1]], torch)
            == tensor_bytes(upsampled[[0, -1]], torch),
            "observed upsampling endpoints differ: " + route,
        )
        endpoints[route] = "PASS"
    for item in capture.inventory:
        data = tensor_bytes(capture.values[item["name"]], torch)
        path = args.output / (item["name"] + ".f32le")
        path.write_bytes(data)
        item["file"] = path.name
        item["size_bytes"] = len(data)
    # Existing global-0 sentinel is an independent previously committed comparator.
    expected_cell = next(
        c
        for c in baseline["cells"]
        if c["runtime"] == "release"
        and c["batch_size"] == 32
        and c["repeat"] == 1
        and c["case"] == "global-0"
    )
    inventory = {i["name"]: i for i in capture.inventory}
    sentinel_checks = {}
    for alias, mapping in document["aliases"]["repeatability-v1"].items():
        actual = (
            sides["captured"]["after"][alias]["selected_sha256"]
            if mapping["kind"] == "input-checkpoint"
            else inventory[mapping["target"]]["sha256"]
        )
        require(
            actual == expected_cell["artifacts"][alias]["sha256"],
            "sentinel drift: " + alias,
        )
        sentinel_checks[alias] = "PASS"
    return {
        "status": "PASS",
        "schema_version": 1,
        "scope": "One global-0 sound in supported batch-32; passive prototype only",
        "case": {
            "sound_index": 0,
            "batch_index": 0,
            "slot": 0,
            "batch_size": 32,
            "reproducible": True,
            "noise_seed": 13,
            "noise_slot": 0,
        },
        "configuration": plan["configuration"],
        "configuration_sha256": digest(json_bytes(plan["configuration"])),
        "runtime_profile": "release-mkl-compatible-v1",
        "runtime": runtime,
        "runtime_sha256": digest(json_bytes(runtime)),
        "source_sha256": source,
        "source_validated_before_import": True,
        "rng_sentinel": "PASS",
        "registry_token": registry.registry_token(),
        "registry_sha256": digest(registry.REGISTRY_PATH.read_bytes()),
        "schema_sha256": document["schema_sha256"],
        "parameter_names": sorted(after["normalized"]["values"]),
        "executions": sides,
        "capture_inventory": capture.inventory,
        "invocation_counts": capture.tracker.counts,
        "passive_capture_equal_bytes": True,
        "previous_sentinel_comparison": sentinel_checks,
        "upsampling_endpoint_checks": endpoints,
        "negative_controls": registry.negative_controls(document),
        "producer_sha256": {
            str(p.relative_to(ROOT)): digest(p.read_bytes())
            for p in (
                Path(__file__),
                ROOT / "src/torchsynth_voice/trace_registry.py",
                ROOT / "env/release-era/probe.py",
                ROOT / "env/release-era/qualify_repeatability.py",
            )
        },
        "limitations": [
            "Not production selective capture or corpus/holdout evidence",
            "No scalar, RTL, hardware playback or fidelity claim",
            "Analytic range metadata is not measured activation coverage",
        ],
    }


def host_run(args):
    require(
        platform.system() == "Darwin" and platform.machine() == "arm64",
        "unqualified host",
    )
    cpu = subprocess.check_output(
        ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
    ).strip()
    os_version = subprocess.check_output(
        ["sw_vers", "-productVersion"], text=True
    ).strip()
    server = subprocess.check_output(
        [
            "docker",
            "version",
            "--format",
            "{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}",
        ],
        text=True,
    ).strip()
    require(
        cpu == "Apple M5" and os_version == "26.5.1" and server == "linux/arm64 29.7.2",
        "host outside DR-0006 measured scope",
    )
    output = args.output.resolve()
    require(
        not output.exists(),
        "output directory already exists; preserve earlier experiment",
    )
    output.mkdir(parents=True)
    build = [
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "--iidfile",
        str(output / "image-id"),
        "-f",
        str(ROOT / "env/release-era/Dockerfile"),
        str(ROOT),
    ]
    with (output / "build.log").open("w") as log:
        subprocess.run(
            build, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=1800
        )
    image = (output / "image-id").read_text().strip()
    image_info = json.loads(
        subprocess.check_output(["docker", "image", "inspect", image])
    )[0]
    require(
        image_info["Architecture"] == "amd64" and image_info["Os"] == "linux",
        "wrong image platform",
    )
    command = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--memory",
        "6g",
        "--cpus",
        "1",
    ]
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        command += ["--env", name + "=1"]
    command += [
        "--env",
        "MKL_CBWR=COMPATIBLE",
        "--mount",
        "type=bind,src=" + str(ROOT) + ",dst=/repo,readonly",
        "--mount",
        "type=bind,src=" + str(output) + ",dst=/output",
        "--entrypoint",
        "env",
        image,
        "-u",
        "ATEN_CPU_CAPABILITY",
        "python",
        "/repo/tools/probe_trace_registry.py",
        "--worker",
        "--output",
        "/output",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    (output / "worker.stdout").write_text(result.stdout)
    (output / "worker.stderr").write_text(result.stderr)
    require(
        result.returncode == 0,
        "worker failed; retained stdout/stderr in " + str(output),
    )
    report = registry.loads((output / "worker.json").read_bytes())
    require(report["status"] == "PASS", "worker did not pass")
    report["host"] = {
        "cpu": cpu,
        "os_version": os_version,
        "platform": platform.platform(),
        "docker_server": server,
    }
    report["launch"] = {
        "command": command,
        "exit_code": result.returncode,
        "stderr": result.stderr,
        "build_command": build,
        "build_log_sha256": digest((output / "build.log").read_bytes()),
        "image_id": image,
        "image_layers": image_info["RootFS"]["Layers"],
        "build_warnings": [
            line
            for line in (output / "build.log").read_text().splitlines()
            if "warning" in line.lower()
        ],
    }
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=ROOT)
    untracked = (
        subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    untracked_files = {
        name: digest((ROOT / name).read_bytes()) for name in untracked if name
    }
    report["project_git"] = {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dirty": bool(diff or untracked_files),
        "diff_sha256": digest(diff),
        "untracked_files_sha256": untracked_files,
        "untracked_state_sha256": digest(json_bytes(untracked_files)),
    }
    # Verify every raw capture file after the worker exits; report is not a file-integrity oracle.
    registry.validate_capture(registry.load_registry(), report["capture_inventory"], 32)
    for item in report["capture_inventory"]:
        data = (output / item["file"]).read_bytes()
        require(
            digest(data) == item["sha256"] and len(data) == item["size_bytes"],
            "raw trace integrity failure",
        )
    for side, execution in report["executions"].items():
        data = (output / (side + ".audio.f32le")).read_bytes()
        require(
            len(data) == 32 * 176400 * 4
            and digest(data) == execution["batch_audio_sha256"],
            "raw audio integrity failure",
        )
    write_json(output / "trace-registry-prototype.json", report)
    print(
        json.dumps(
            {
                "status": "PASS",
                "report": str(output / "trace-registry-prototype.json"),
                "traces": len(report["capture_inventory"]),
                "registry_token": report["registry_token"],
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "out/trace-registry-prototype"
    )
    parser.add_argument("--source-root", type=Path, default=Path("/opt/torchsynth"))
    args = parser.parse_args()
    if not args.worker:
        host_run(args)
        return
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
