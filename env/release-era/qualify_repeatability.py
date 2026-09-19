"""Preregistered CPU repeat/batch experiment and actual-render drift sentinel.

Python 3.9 compatible. Torch is imported only inside the source-gated worker.
This is a bounded observation probe, not the production trace adapter.
"""

import argparse
import contextlib
import datetime
import importlib.metadata
import io
import json
import math
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import warnings
from functools import lru_cache
from pathlib import Path

from probe import json_bytes, sha256, validate_source

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PLAN = HERE / "repeatability-matrix.json"
# Explicit replay only: this reviewed revision generated the retained COMPATIBLE
# matrix. Never infer trust from a receipt or accept a caller-provided digest.
HISTORICAL_RUNNER = "2182bc9524016476f9a538d11fe2e3035fe0ae45"
HISTORICAL_RUNNER_SHA256 = (
    "1ad50da4cde6e72ea25327dc828016337dc6cf0cb926fd3488bc92d6188c98b9"
)
THREAD_ENV = {
    name: "1"
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    )
}


def load_plan():
    plan = json.loads(PLAN.read_text())
    validate_plan(plan)
    return plan


def validate_plan(plan):
    names = ["global-" + str(i) for i in (0, 31, 32, 9215, 9216, 39942)]
    names += ["normalization-off", "normalization-on"]
    if (
        plan["batch_sizes"] != [32, 64, 128, 256]
        or plan["repeats"] != [1, 2]
        or [c["name"] for c in plan["cases"]] != names
        or set(plan["runtime_definitions"]) != {"release", "current"}
    ):
        raise ValueError("incomplete preregistered matrix")
    if [c["index"] for c in plan["cases"]] != [0, 31, 32, 9215, 9216, 39942, 0, 0]:
        raise ValueError("incorrect preregistered identities")
    inventory = json.loads(
        (REPO / "spec/reference/parameter-inventory-v1.json").read_text()
    )
    names = sorted(p["name"] for p in inventory["parameters"])
    required = {
        name: {"dtype": "<f4", "shape": [count]}
        for name, count in (
            [(n, 1764) for n in ("adsr_1", "adsr_2", "lfo_1", "lfo_2")]
            + [
                (n, 176400)
                for n in ("vco_1", "vco_2", "noise", "pre_normalization", "audio")
            ]
            + [(n, 78) for n in ("normalized", "physical")]
        )
    }
    if (
        plan["parameter_names"] != names
        or plan["artifacts"] != required
        or set(plan["seams"]) != set(required) - {"normalized", "physical"}
        or plan["sample_count"] != 176400
        or plan["control_sample_count"] != 1764
    ):
        raise ValueError("invalid preregistered artifact contract")


def plan_hash(plan):
    return sha256(json_bytes(plan))


def coordinates(index, size):
    if (
        type(index) is not int
        or index < 0
        or type(size) is not int
        or size <= 0
        or size % 32
    ):
        raise ValueError("invalid reproducible coordinates")
    batch, slot = divmod(index, size)
    return {
        "index": index,
        "batch": batch,
        "slot": slot,
        "batch_size": size,
        "noise_slot": index % 32,
        "is_train": (index // 1024) % 10 != 9,
    }


def expected_cells(plan):
    return [
        {"runtime": runtime, "batch_size": size, "repeat": repeat, "case": case["name"]}
        for runtime in plan["runtime_definitions"]
        for size in plan["batch_sizes"]
        for repeat in plan["repeats"]
        for case in plan["cases"]
    ]


def cell_key(cell):
    return tuple(cell[k] for k in ("runtime", "batch_size", "repeat", "case"))


def validate_results(plan, cells, root=None, *, historical_runner=None):
    keys = [cell_key(c) for c in cells]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate matrix cells")
    if set(keys) != {cell_key(c) for c in expected_cells(plan)}:
        raise ValueError("missing or unexpected matrix cells")
    for cell in cells:
        if cell["plan_sha256"] != plan_hash(plan):
            raise ValueError("stale matrix result")
        if cell["status"] not in ("PASS", "FAIL", "NO_VERDICT"):
            raise ValueError("invalid cell status")
        if cell["status"] != "PASS" and not cell.get("error"):
            raise ValueError("refusal/failure requires a reason")
        if cell["status"] == "PASS":
            validate_cell(plan, cell, root, historical_runner=historical_runner)
    passed = [c for c in cells if c["status"] == "PASS"]
    if len({c["run_id"] for c in passed}) > 1:
        raise ValueError("mixed experiment run IDs")
    executions = {}
    for cell in passed:
        group = cell_key(cell)[:3]
        execution = cell["execution_id"]
        if group in executions and executions[group] != execution:
            raise ValueError("worker group changed execution ID")
        executions[group] = execution
    if len(set(executions.values())) != len(executions):
        raise ValueError("fresh processes reused an execution ID")


def case_inputs(plan, case):
    return {
        "index": case["index"],
        "physical_overrides": case["physical_overrides"],
        "configuration": plan["configuration"],
        "nebula": plan["nebula"],
        "noise_seed": plan["noise_seed"],
    }


def expected_source_hashes():
    manifests = [
        json.loads((REPO / "spec/reference/upstream.json").read_text()),
        json.loads((HERE / "source-comparison.json").read_text())["selected"],
    ]
    return {
        k: v
        for m in manifests
        for group in ("files", "source_checkout_only_files")
        for k, v in m.get(group, {}).items()
    }


@lru_cache(maxsize=1)
def historical_runner_hash(revision):
    if revision != HISTORICAL_RUNNER:
        raise ValueError("unreviewed historical runner revision")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "show",
            revision + ":env/release-era/qualify_repeatability.py",
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(
            "historical runner Git object unavailable; explicitly acquire "
            + revision
            + " (see REPEATABILITY.md); qualification never downloads it"
        )
    if sha256(result.stdout) != HISTORICAL_RUNNER_SHA256:
        raise ValueError("historical runner Git-object hash mismatch")
    return HISTORICAL_RUNNER_SHA256


def validate_runtime_identity(plan, runtime, observed):
    """Check mandatory recorded scope, not execution attestation/portability."""
    machine, prefix = (
        ("x86_64", "Linux-") if runtime == "release" else ("arm64", "macOS-")
    )
    if (
        any(
            not isinstance(observed.get(k), str) or not observed[k].strip()
            for k in ("machine", "platform", "cpu", "torch_build")
        )
        or observed["machine"] != machine
        or not observed["platform"].startswith(prefix)
    ):
        raise ValueError("worker runtime identity missing or outside recorded platform")
    packages = observed.get("packages")
    if (
        not isinstance(packages, dict)
        or not packages
        or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            for k, v in packages.items()
        )
    ):
        raise ValueError("worker package identity missing or malformed")
    definition = plan["runtime_definitions"][runtime]
    lock = (REPO / definition["lock"]).read_bytes()
    if sha256(lock) != definition["lock_sha256"]:
        raise ValueError("runtime lock hash mismatch")
    if any(packages.get(k) != definition[k] for k in ("torch", "numpy", "lightning")):
        raise ValueError("worker package/runtime identity mismatch")
    if runtime == "release":
        required = dict(
            re.findall(
                r"^([\w-]+)(?:\[[^]]+\])?==([^\s]+)", lock.decode(), re.MULTILINE
            )
        )
        if any(packages.get(k) != v for k, v in required.items()):
            raise ValueError("worker package/lock identity mismatch")
    else:
        # This fixed, hash-checked uv.lock shape can be read by Python 3.9 too.
        permitted = set(
            re.findall(
                r'\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', lock.decode()
            )
        )
        if not permitted or any((k, v) not in permitted for k, v in packages.items()):
            raise ValueError("worker package/lock identity mismatch")


def validate_render_command(cell, worker, receipt, provenance):
    command = receipt.get("command")
    if (
        not isinstance(command, list)
        or len(command) < 2
        or any(not isinstance(arg, str) or not arg for arg in command)
    ):
        raise ValueError("render command missing or malformed")
    runtime = cell["runtime"]
    script = (
        "/repo/env/release-era/qualify_repeatability.py"
        if runtime == "release"
        else command[1]
    )
    if (
        not script.endswith("/env/release-era/qualify_repeatability.py")
        or command.count(script) != 1
    ):
        raise ValueError("render command runner mismatch")
    start = command.index(script)
    suffix = [
        "worker",
        "--runtime",
        runtime,
        "--batch-size",
        str(cell["batch_size"]),
        "--repeat",
        str(cell["repeat"]),
        "--run-id",
        cell["run_id"],
    ]
    sentinel = "--sentinel" in command[start + 1 :]
    if sentinel:
        suffix.append("--sentinel")
    expected_cases = (
        ["global-0"] if sentinel else [c["name"] for c in load_plan()["cases"]]
    )
    if [c["case"] for c in worker["cells"]] != expected_cases or any(
        cell_key(c)[:3] != cell_key(cell)[:3]
        or c.get("run_id") != cell["run_id"]
        or c.get("execution_id") != worker["execution_id"]
        for c in worker["cells"]
    ):
        raise ValueError("render command worker case/group mismatch")
    tail = command[start + 1 :]
    if (
        len(tail) != len(suffix) + 4
        or tail[: len(suffix)] != suffix
        or tail[-4] != "--source-root"
        or tail[-2] != "--output"
    ):
        raise ValueError("render command role/arguments mismatch")
    source, output = tail[-3], tail[-1]
    if runtime == "current":
        if (
            start != 1
            or not re.fullmatch(
                r"python(?:[0-9]+(?:\.[0-9]+)*)?", Path(command[0]).name
            )
            or not Path(source).is_absolute()
            or not Path(output).is_absolute()
            or Path(output).name != cell["directory"]
        ):
            raise ValueError("render command current paths mismatch")
        return
    if (source, output) != ("/opt/torchsynth", "/output"):
        raise ValueError("render command container paths mismatch")
    image = provenance.get("image", {}).get("Id")
    if not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("render command image identity missing")
    mounts = [command[i + 1] for i, v in enumerate(command[:-1]) if v == "--mount"]
    if (
        len(mounts) != 2
        or not mounts[0].startswith("type=bind,src=/")
        or not mounts[0].endswith(",dst=/repo,readonly")
        or not mounts[1].startswith("type=bind,src=/")
        or not mounts[1].endswith(",dst=/output")
    ):
        raise ValueError("render command mount mismatch")
    output_path = Path(mounts[1][len("type=bind,src=") : -len(",dst=/output")])
    if output_path.name != cell["directory"]:
        raise ValueError("render command output binding mismatch")
    expected = command_for(
        argparse.Namespace(mode="sentinel" if sentinel else "matrix", image=image),
        runtime,
        cell["batch_size"],
        cell["repeat"],
        output_path,
        cell["run_id"],
    )
    # Container names and absolute host locators vary, including after relocation.
    # All executable, role, image, isolation, profile and worker arguments do not.
    if "--name" not in command or command.index("--name") + 1 >= len(command):
        raise ValueError("render command container identity missing")
    expected[expected.index("--name") + 1] = command[command.index("--name") + 1]
    expected[expected.index("--mount") + 1] = mounts[0]
    if command != expected:
        raise ValueError("render command launch/profile binding mismatch")


def validate_process_receipt(
    plan, cell, worker, receipt, provenance, historical_runner
):
    trusted = (
        sha256(Path(__file__).read_bytes())
        if historical_runner is None
        else historical_runner_hash(historical_runner)
    )
    if (
        worker.get("runner_sha256") != trusted
        or provenance.get("runner_sha256") != trusted
    ):
        raise ValueError("worker runner identity differs from reviewed code")
    if type(worker.get("process_id")) is not int or worker["process_id"] <= 0:
        raise ValueError("worker process metadata requires a positive PID")
    try:
        started = datetime.datetime.fromisoformat(worker["started_utc"])
        execution = uuid.UUID(worker["execution_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("worker process metadata missing or malformed") from exc
    if (
        started.utcoffset() != datetime.timedelta(0)
        or str(execution) != worker["execution_id"]
        or execution.version != 4
    ):
        raise ValueError("worker process metadata requires UTC/UUID identity")
    preregistered = datetime.datetime.fromisoformat(
        plan["preregistered_utc"].replace("Z", "+00:00")  # noqa: FURB162 -- Python 3.9 cannot parse Z.
    )
    if started < preregistered:
        raise ValueError("worker process start predates preregistration")
    if receipt.get("worker") != {k: v for k, v in worker.items() if k != "cells"}:
        raise ValueError("duplicated worker receipt mismatch")
    validate_runtime_identity(plan, cell["runtime"], worker["runtime"])
    validate_render_command(cell, worker, receipt, provenance)


def validate_cell(plan, cell, root=None, *, historical_runner=None):
    """Refuse incomplete observations before any aggregate or byte credit."""
    if cell.get("status") != "PASS":
        raise ValueError("render unavailable")
    if cell_key(cell) not in {cell_key(c) for c in expected_cells(plan)}:
        raise ValueError("unexpected cell coordinates")
    case = next(c for c in plan["cases"] if c["name"] == cell["case"])
    identity = coordinates(case["index"], cell["batch_size"])
    inputs = case_inputs(plan, case)
    if cell.get("identity") != identity:
        raise ValueError("cell identity mismatch")
    if cell.get("inputs") != inputs or cell.get("input_sha256") != sha256(
        json_bytes(inputs)
    ):
        raise ValueError("cell input/configuration mismatch")
    if cell.get("plan_sha256") != plan_hash(plan):
        raise ValueError("stale matrix result")
    if cell.get("label_byte_hex") != bytes([int(identity["is_train"])]).hex():
        raise ValueError("cell label mismatch")
    names = plan["parameter_names"]
    if cell.get("parameter_names") != names:
        raise ValueError("parameter names mismatch")
    for kind in ("normalized", "physical"):
        values = cell.get(kind, {})
        if set(values) != set(names) or not all(
            math.isfinite(v) for v in values.values()
        ):
            raise ValueError("parameter map mismatch")
    if set(cell.get("artifacts", {})) != set(plan["artifacts"]):
        raise ValueError("required artifact set mismatch")
    for name, spec in plan["artifacts"].items():
        record = cell["artifacts"][name]
        if (
            record.get("samples") != spec["shape"][0]
            or record.get("shape") != spec["shape"]
            or record.get("dtype") != spec["dtype"]
            or record.get("file") != case["name"] + "." + name + ".f32le"
        ):
            raise ValueError("artifact count/shape/dtype/name mismatch: " + name)
    if not cell.get("run_id") or not cell.get("execution_id"):
        raise ValueError("missing process identity")
    if root is None:
        return None
    provenance = json.loads((root / "provenance.json").read_text())
    if (
        provenance.get("plan_sha256") != plan_hash(plan)
        or provenance.get("run_id") != cell["run_id"]
    ):
        raise ValueError("controller/cell binding mismatch")
    directory = "{}-{}-{}".format(cell["runtime"], cell["batch_size"], cell["repeat"])
    if cell.get("directory") != directory:
        raise ValueError("cell directory mismatch")
    worker = json.loads((root / directory / "result.json").read_text())
    receipt = json.loads((root / directory / "execution.json").read_text())
    stdout = (root / directory / "stdout.json").read_bytes()
    if (
        receipt.get("exit_code") != 0
        or receipt.get("directory") != directory
        or receipt.get("stdout_sha256") != sha256(stdout)
        or json.loads(stdout) != worker
    ):
        raise ValueError("worker execution receipt mismatch")
    matches = [c for c in worker["cells"] if cell_key(c) == cell_key(cell)]
    if matches != [{k: v for k, v in cell.items() if k != "directory"}]:
        raise ValueError("cell/worker result mismatch")
    definition = plan["runtime_definitions"][cell["runtime"]]
    observed = worker["runtime"]
    if (
        worker.get("run_id") != cell["run_id"]
        or worker.get("execution_id") != cell["execution_id"]
        or worker.get("plan_sha256") != plan_hash(plan)
        or worker.get("source_sha256") != expected_source_hashes()
        or worker.get("source_validated_before_import") is not True
        or worker.get("rng_sentinel") != "PASS"
        or worker.get("runner_sha256") != provenance["runner_sha256"]
        or any(
            observed.get(k) != definition[k]
            for k in ("python", "torch", "numpy", "lightning", "lock_sha256")
        )
        or observed.get("math_environment")
        != plan["profile_environment"][cell["runtime"]]
        or observed.get("thread_environment") != THREAD_ENV
        or observed.get("threads") != 1
        or observed.get("interop_threads") != 1
    ):
        raise ValueError("worker source/runtime/process binding mismatch")
    validate_process_receipt(plan, cell, worker, receipt, provenance, historical_runner)
    artifacts = {
        name: read_artifact(root / directory, cell["artifacts"][name])
        for name in plan["artifacts"]
    }
    for name, data in artifacts.items():
        if not all(math.isfinite(v[0]) for v in struct.iter_unpack("<f", data)):
            raise ValueError("nonfinite artifact: " + name)
    for kind in ("normalized", "physical"):
        if artifacts[kind] != struct.pack("<78f", *[cell[kind][n] for n in names]):
            raise ValueError("parameter map/raw bytes mismatch")
    peak = max(
        abs(v[0]) for v in struct.iter_unpack("<f", artifacts["pre_normalization"])
    )
    if (
        cell.get("normalization_applied") != (peak > 1)
        or (case["name"] == "normalization-off" and not 0 < peak <= 1)
        or (case["name"] == "normalization-on" and peak <= 1)
    ):
        raise ValueError("normalization observation mismatch")
    return artifacts


def verdict(statuses):
    if "FAIL" in statuses:
        return "FAIL"
    return "PASS" if statuses and all(s == "PASS" for s in statuses) else "NO_VERDICT"


def compare_bytes(left, right):
    if not left or len(left) % 4 or len(left) != len(right):
        raise ValueError("empty, malformed or unequal float32 sample count")
    a = [v[0] for v in struct.iter_unpack("<f", left)]
    b = [v[0] for v in struct.iter_unpack("<f", right)]
    if not all(math.isfinite(v) for v in a + b):
        raise ValueError("nonfinite sample")
    first_byte = next((i for i, (x, y) in enumerate(zip(left, right)) if x != y), None)
    differences = [abs(x - y) for x, y in zip(a, b)]
    first = None if first_byte is None else first_byte // 4
    return {
        "equal_bytes": left == right,
        "samples": len(a),
        "first_different_byte": first_byte,
        "first_different_sample": first,
        "first_values": None if first is None else [a[first], b[first]],
        "max_abs_difference": max(differences),
        "mean_abs_difference": math.fsum(differences) / len(a),
        "rms_difference": math.sqrt(math.fsum(v * v for v in differences) / len(a)),
    }


def read_artifact(root, record):
    data = (root / record["file"]).read_bytes()
    if len(data) != record["samples"] * 4:
        raise ValueError("artifact sample count mismatch")
    if sha256(data) != record["sha256"]:
        raise ValueError("artifact hash mismatch")
    return data


def source_gate(source):
    if "torch" in sys.modules or "torchsynth" in sys.modules:
        raise ValueError("Torch imported before source validation")
    manifest = json.loads((REPO / "spec/reference/upstream.json").read_text())
    hashes = validate_source(source, manifest)
    selected = json.loads((HERE / "source-comparison.json").read_text())["selected"]
    hashes.update(validate_source(source, selected))
    return hashes


def runtime_record(runtime, plan, torch, packages):
    definition = plan["runtime_definitions"][runtime]
    lock = REPO / definition["lock"]
    if sha256(lock.read_bytes()) != definition["lock_sha256"]:
        raise ValueError("runtime lock hash mismatch")
    actual = {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "numpy": packages["numpy"],
        "lightning": packages["lightning"],
    }
    if any(actual[k] != definition[k] for k in actual):
        raise ValueError("runtime version mismatch: " + str(actual))
    system, machine = platform.system(), platform.machine()
    if (
        runtime == "release"
        and (system, machine) != ("Linux", "x86_64")
        or runtime == "current"
        and (system, machine) != ("Darwin", "arm64")
    ):
        raise ValueError("runtime platform outside preregistered scope")
    if runtime == "release":
        if (
            sha256((HERE / "Dockerfile").read_bytes())
            != definition["dockerfile_sha256"]
        ):
            raise ValueError("runtime Dockerfile hash mismatch")
        locked = dict(
            re.findall(
                r"^([\w-]+)(?:\[[^]]+\])?==([^\s]+)", lock.read_text(), re.MULTILINE
            )
        )
        # The direct, hashed torch wheel is validated by the immutable image build.
        locked["torch"] = definition["torch"]
        if any(packages.get(k) != v for k, v in locked.items()):
            raise ValueError("installed release package differs from lock")
    else:
        import tomllib

        locked = tomllib.loads(lock.read_text())["package"]
        permitted = {(p["name"], p["version"]) for p in locked}
        mismatches = [(k, v) for k, v in packages.items() if (k, v) not in permitted]
        if mismatches:
            raise ValueError(
                "installed current package absent from lock: " + str(mismatches)
            )
    cpu = platform.processor()
    if Path("/proc/cpuinfo").exists():
        cpu = Path("/proc/cpuinfo").read_text()
    elif platform.system() == "Darwin":
        cpu = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip()
    return dict(
        actual,
        packages=packages,
        platform=platform.platform(),
        machine=platform.machine(),
        cpu=cpu,
        lock_sha256=definition["lock_sha256"],
        torch_build=torch.__config__.show(),
        threads=torch.get_num_threads(),
        interop_threads=torch.get_num_interop_threads(),
        thread_environment={k: os.environ.get(k) for k in THREAD_ENV},
        math_environment={
            k: os.environ.get(k) for k in plan["profile_environment"][runtime]
        },
    )


def render_case(case, size, plan, output, torch, np, Voice, SynthConfig, normalize):
    identity = coordinates(case["index"], size)
    slot = identity["slot"]

    def make_voice():
        voice = (
            Voice(
                SynthConfig(batch_size=size, **plan["configuration"]), nebula="default"
            )
            .cpu()
            .eval()
        )
        for name, value in case["physical_overrides"].items():
            voice.set_parameters(
                {tuple(name.split(".")): torch.full((size,), value)}, freeze=True
            )
        return voice

    voice = make_voice()
    captured, handles = {}, []
    for name in plan["seams"]:
        if name in ("audio", "pre_normalization"):
            continue

        def capture(module, args, result, name=name):
            captured[name] = result[slot].detach().clone()

        handles.append(getattr(voice, name).register_forward_hook(capture))

    def profile(frame, event, arg):
        if event == "call" and frame.f_code is normalize.__code__:
            captured["pre_normalization"] = (
                frame.f_locals["signal"][slot].detach().clone()
            )

    sys.setprofile(profile)
    try:
        with torch.inference_mode():
            audio, forward, labels = voice(identity["batch"])
    finally:
        sys.setprofile(None)
        for handle in handles:
            handle.remove()
    captured["audio"] = audio[slot].detach().clone()
    if not torch.equal(captured["noise"], voice.noise.noise[identity["noise_slot"]]):
        raise ValueError("selected noise stream differs from index modulo 32")
    parameters = voice.get_parameters(include_frozen=True)
    normalized = {".".join(k): float(p.detach()[slot]) for k, p in parameters.items()}
    physical = {
        ".".join(k): float(p.from_0to1().detach()[slot]) for k, p in parameters.items()
    }
    expected_names = {
        ".".join(p["name"][:2])
        for p in json.loads(
            (output["source"] / "torchsynth/nebulae/voice/default.json").read_text()
        )
    }
    if set(normalized) != expected_names or len(normalized) != 78:
        raise ValueError("named parameter set mismatch")
    if forward.shape != (size, 78) or audio.shape != (size, plan["sample_count"]):
        raise ValueError("forward shape/count mismatch")
    if labels.shape != (size,) or bool(labels[slot]) != identity["is_train"]:
        raise ValueError("train/test mapping mismatch")
    if (
        audio.dtype != torch.float32
        or forward.dtype != torch.float32
        or labels.dtype != torch.bool
    ):
        raise ValueError("forward dtype mismatch")
    by_id = {id(p): ".".join(k) for k, p in parameters.items()}
    if forward[slot].tolist() != [normalized[by_id[id(p)]] for p in voice.parameters()]:
        raise ValueError("forward/named parameters mismatch")
    artifacts = {}
    for name, tensor in captured.items():
        if tensor.dtype != torch.float32:
            raise ValueError("trace dtype mismatch: " + name)
        array = tensor.cpu().contiguous().numpy().astype("<f4", copy=False)
        count = (
            plan["control_sample_count"]
            if name.startswith(("adsr", "lfo"))
            else plan["sample_count"]
        )
        if array.shape != (count,) or not np.isfinite(array).all():
            raise ValueError("trace shape/count/nonfinite: " + name)
        data = array.tobytes()
        path = output["directory"] / (case["name"] + "." + name + ".f32le")
        path.write_bytes(data)
        artifacts[name] = {
            "file": path.name,
            "sha256": sha256(data),
            "samples": count,
            "peak": float(np.abs(array).max()),
            **plan["artifacts"][name],
        }
    for kind, values in (("normalized", normalized), ("physical", physical)):
        data = struct.pack("<78f", *[values[k] for k in sorted(values)])
        if not all(math.isfinite(v) for v in values.values()):
            raise ValueError("nonfinite parameter")
        path = output["directory"] / (case["name"] + "." + kind + ".f32le")
        path.write_bytes(data)
        artifacts[kind] = {
            "file": path.name,
            "sha256": sha256(data),
            "samples": 78,
            **plan["artifacts"][kind],
        }
    peak = artifacts["pre_normalization"]["peak"]
    if (
        case["name"] == "normalization-off"
        and not 0 < peak <= 1
        or case["name"] == "normalization-on"
        and peak <= 1
    ):
        raise ValueError("requested normalization branch not exercised")
    passive = None
    if size == 32 and case["name"] == "global-0":
        expected_audio = captured["audio"].numpy().astype("<f4", copy=False).tobytes()
        del voice, audio, forward, parameters
        control = make_voice()
        with torch.inference_mode():
            unhooked = (
                control(identity["batch"])[0][slot]
                .numpy()
                .astype("<f4", copy=False)
                .tobytes()
            )
        passive = compare_bytes(expected_audio, unhooked)
        if not passive["equal_bytes"]:
            raise ValueError("passive capture changed audio")
    inputs = {
        "index": case["index"],
        "physical_overrides": case["physical_overrides"],
        "configuration": plan["configuration"],
        "nebula": plan["nebula"],
        "noise_seed": plan["noise_seed"],
    }
    return {
        "identity": identity,
        "inputs": inputs,
        "input_sha256": sha256(json_bytes(inputs)),
        "parameter_names": sorted(normalized),
        "normalized": normalized,
        "physical": physical,
        "label_byte_hex": bytes([int(labels[slot])]).hex(),
        "artifacts": artifacts,
        "normalization_applied": peak > 1,
        "passive_capture": passive,
    }


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def worker(args):
    plan = load_plan()
    hashes = source_gate(args.source_root)
    if args.preflight_only:
        return {"status": "PASS", "source_sha256": hashes, "torch_imported": False}
    # Refuse incidental host inheritance before importing any numerical package.
    expected_env = dict(THREAD_ENV, **plan["profile_environment"][args.runtime])
    if any(os.environ.get(k) != v for k, v in expected_env.items()):
        raise ValueError("worker environment outside explicit runtime profile")
    # setuptools may append its vendored distributions to sys.path on import.
    # Inventory the installed environment before any third-party import.
    packages = dict(
        sorted(
            (d.metadata["Name"].lower().replace("_", "-"), d.version)
            for d in importlib.metadata.distributions()
        )
    )
    sys.path.insert(0, str(args.source_root))
    import numpy as np
    import torch
    from torchsynth.config import SynthConfig, check_for_reproducibility
    from torchsynth.synth import Voice
    from torchsynth.util import normalize_if_clipping

    if (
        Path(sys.modules["torchsynth.synth"].__file__).resolve()
        != (args.source_root / "torchsynth/synth.py").resolve()
    ):
        raise ValueError("import did not use validated source")
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    check_for_reproducibility()
    runtime = runtime_record(args.runtime, plan, torch, packages)
    report = {
        "run_id": args.run_id,
        "plan_sha256": plan_hash(plan),
        "runner_sha256": sha256(Path(__file__).read_bytes()),
        "execution_id": str(uuid.uuid4()),
        "process_id": os.getpid(),
        # datetime.UTC is unavailable in the pinned Python 3.9 worker.
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),  # noqa: UP017
        "runtime": runtime,
        "source_sha256": hashes,
        "source_validated_before_import": True,
        "rng_sentinel": "PASS",
        "cells": [],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    for case in plan["cases"]:
        if args.sentinel and case["name"] != "global-0":
            continue
        cell = {
            "runtime": args.runtime,
            "batch_size": args.batch_size,
            "repeat": args.repeat,
            "case": case["name"],
            "plan_sha256": plan_hash(plan),
            "run_id": report["run_id"],
            "execution_id": report["execution_id"],
        }
        start = time.monotonic()
        try:
            cell.update(
                render_case(
                    case,
                    args.batch_size,
                    plan,
                    {"directory": args.output, "source": args.source_root},
                    torch,
                    np,
                    Voice,
                    SynthConfig,
                    normalize_if_clipping,
                )
            )
            cell["status"] = "PASS"
        except Exception as exc:  # noqa: BLE001 -- Preserve every worker failure as explicit evidence.
            cell.update(status="NO_VERDICT", error=type(exc).__name__ + ": " + str(exc))
        cell["elapsed_seconds"] = time.monotonic() - start
        report["cells"].append(cell)
        write_json(args.output / "result.json", report)
    return report


def pair(left, right, root, plan=None, *, historical_runner=None):
    if left["status"] != "PASS" or right["status"] != "PASS":
        return {
            "status": "NO_VERDICT",
            "reason": "one or both render cells unavailable",
        }
    plan = load_plan() if plan is None else plan
    a = validate_cell(plan, left, root, historical_runner=historical_runner)
    b = validate_cell(plan, right, root, historical_runner=historical_runner)
    compared = {name: compare_bytes(a[name], b[name]) for name in plan["artifacts"]}
    inputs_equal = (
        left["input_sha256"] == right["input_sha256"]
        and left["parameter_names"] == right["parameter_names"]
        and left["label_byte_hex"] == right["label_byte_hex"]
    )
    return {
        "status": "PASS"
        if inputs_equal and all(v["equal_bytes"] for v in compared.values())
        else "FAIL",
        "inputs_and_label_equal": inputs_equal,
        "artifacts": compared,
    }


def summarize(plan, cells, root, *, historical_runner=None):
    validate_results(plan, cells, root, historical_runner=historical_runner)
    lookup = {cell_key(c): c for c in cells}
    comparisons = []
    for runtime in plan["runtime_definitions"]:
        for size in plan["batch_sizes"]:
            for case in plan["cases"]:
                name = case["name"]
                pairs = [("repeat", (runtime, size, 1, name), (runtime, size, 2, name))]
                if size != 32:
                    pairs.append(
                        ("batch", (runtime, 32, 1, name), (runtime, size, 1, name))
                    )
                if runtime == "release":
                    pairs.append(
                        (
                            "cross_runtime",
                            (runtime, size, 1, name),
                            ("current", size, 1, name),
                        )
                    )
                for kind, a, b in pairs:
                    comparisons.append(
                        dict(
                            kind=kind,
                            left=list(a),
                            right=list(b),
                            **pair(
                                lookup[a],
                                lookup[b],
                                root,
                                plan,
                                historical_runner=historical_runner,
                            ),
                        )
                    )
    return comparisons


def check_sentinel(cell, expected, root, *, historical_runner=None):
    if root is None:
        raise ValueError("sentinel requires verified raw files")
    if cell["status"] != "PASS":
        raise ValueError("sentinel render unavailable: " + cell.get("error", "unknown"))
    plan = load_plan()
    validate_cell(plan, cell, root, historical_runner=historical_runner)
    if cell_key(cell) != ("release", 32, 1, "global-0"):
        raise ValueError("sentinel coordinates mismatch")
    if cell["input_sha256"] != expected["input_sha256"]:
        raise ValueError("sentinel input mismatch")
    if cell["plan_sha256"] != expected["plan_sha256"]:
        raise ValueError("sentinel preregistration mismatch")
    if {k: v["sha256"] for k, v in cell["artifacts"].items()} != expected[
        "artifact_sha256"
    ]:
        raise ValueError("sentinel artifact hash mismatch")
    if cell["label_byte_hex"] != expected["label_byte_hex"]:
        raise ValueError("sentinel label mismatch")
    if not cell["passive_capture"]["equal_bytes"]:
        raise ValueError("sentinel passive capture mismatch")


def invoke(command, timeout=1800, profile=None):
    """Unavailable executables and timeouts are results, never missing cells."""
    try:
        environment = dict(os.environ, **THREAD_ENV, PYTHONDONTWRITEBYTECODE="1")
        for key, value in (profile or {}).items():
            environment.pop(key, None)
            if value is not None:
                environment[key] = value
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, subprocess.TimeoutExpired) and command[:2] == [
            "docker",
            "run",
        ]:
            # Stop only this experiment's named container if its CLI timed out.
            name = command[command.index("--name") + 1]
            subprocess.run(
                ["docker", "rm", "--force", name],
                capture_output=True,
                check=False,
                timeout=30,
            )
        return subprocess.CompletedProcess(
            command, -1, "", type(exc).__name__ + ": " + str(exc)
        )


def command_for(args, runtime, size, repeat, directory, run_id):
    profile = load_plan()["profile_environment"][runtime]
    suffix = [
        "worker",
        "--runtime",
        runtime,
        "--batch-size",
        str(size),
        "--repeat",
        str(repeat),
        "--run-id",
        run_id,
    ]
    if args.mode == "sentinel":
        suffix.append("--sentinel")
    if runtime == "release":
        return [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--name",
            "repeatability-" + uuid.uuid4().hex,
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            *[part for k, v in THREAD_ENV.items() for part in ("--env", k + "=" + v)],
            *[
                part
                for k, v in profile.items()
                if v is not None
                for part in ("--env", k + "=" + v)
            ],
            "--memory",
            "6g",
            "--cpus",
            "1",
            "--mount",
            "type=bind,src=" + str(REPO) + ",dst=/repo,readonly",
            "--mount",
            "type=bind,src=" + str(directory) + ",dst=/output",
            "--entrypoint",
            "env",
            args.image,
            *[part for k, v in profile.items() if v is None for part in ("-u", k)],
            "python",
            "/repo/env/release-era/qualify_repeatability.py",
            *suffix,
            "--source-root",
            "/opt/torchsynth",
            "--output",
            "/output",
        ]
    return [
        args.current_python,
        str(Path(__file__).resolve()),
        *suffix,
        "--source-root",
        str(args.source_root),
        "--output",
        str(directory),
    ]


def run(args):
    plan = load_plan()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "preregistered-plan.json", plan)
    run_id = str(uuid.uuid4())
    runtimes = (
        ["release"] if args.mode == "sentinel" else list(plan["runtime_definitions"])
    )
    sizes = [32] if args.mode == "sentinel" else plan["batch_sizes"]
    repeats = [1] if args.mode == "sentinel" else plan["repeats"]
    records, cells, controls = [], [], {}
    image = invoke(["docker", "image", "inspect", args.image], timeout=60)
    docker = invoke(
        [
            "docker",
            "version",
            "--format",
            "{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}",
        ],
        timeout=60,
    )
    provenance = {
        "image": json.loads(image.stdout)[0] if image.returncode == 0 else image.stderr,
        "docker_server": docker.stdout.strip(),
        "host_platform": platform.platform(),
        "runner_sha256": sha256(Path(__file__).read_bytes()),
        "plan_sha256": plan_hash(plan),
        "run_id": run_id,
    }
    write_json(root / "provenance.json", provenance)
    for runtime in runtimes:
        for size in sizes:
            for repeat in repeats:
                relative = f"{runtime}-{size}-{repeat}"
                directory = root / relative
                directory.mkdir()
                command = command_for(args, runtime, size, repeat, directory, run_id)
                if runtime not in controls:
                    negative_command = list(command)
                    negative_command[negative_command.index("worker")] = "negative"
                    negative = invoke(
                        negative_command,
                        timeout=120,
                        profile=plan["profile_environment"][runtime],
                    )
                    controls[runtime] = {
                        "command": negative_command,
                        "result": json.loads(negative.stdout)
                        if negative.returncode == 0
                        else {
                            "status": "NO_VERDICT",
                            "error": negative.stdout + negative.stderr,
                        },
                    }
                print("Rendering " + relative, flush=True)
                started = time.monotonic()
                result = invoke(command, profile=plan["profile_environment"][runtime])
                stdout, stderr, code = result.stdout, result.stderr, result.returncode
                (directory / "stdout.json").write_text(stdout)
                (directory / "stderr.txt").write_text(stderr)
                record = {
                    "directory": relative,
                    "command": command,
                    "exit_code": code,
                    "elapsed_seconds": time.monotonic() - started,
                    "stdout_sha256": sha256(stdout.encode()),
                    "stderr": stderr,
                }
                result_path = directory / "result.json"
                data = (
                    json.loads(result_path.read_text()) if result_path.exists() else {}
                )
                if data and data.get("run_id") != run_id:
                    raise ValueError("stale worker result")
                record["worker"] = {k: v for k, v in data.items() if k != "cells"}
                write_json(directory / "execution.json", record)
                observed = {c["case"]: c for c in data.get("cells", [])}
                if len(observed) != len(data.get("cells", [])):
                    raise ValueError("duplicate worker cells")
                allowed = {
                    c["name"]
                    for c in plan["cases"]
                    if args.mode != "sentinel" or c["name"] == "global-0"
                }
                if set(observed) - allowed:
                    raise ValueError("unexpected worker cells")
                for case in plan["cases"]:
                    if args.mode == "sentinel" and case["name"] != "global-0":
                        continue
                    cell = observed.get(
                        case["name"],
                        {
                            "runtime": runtime,
                            "batch_size": size,
                            "repeat": repeat,
                            "case": case["name"],
                            "plan_sha256": plan_hash(plan),
                            "status": "NO_VERDICT",
                            "error": f"worker exit {code}: {stdout[-2000:]} {stderr[-2000:]}",
                        },
                    )
                    if cell_key(cell) != (runtime, size, repeat, case["name"]):
                        raise ValueError("worker/cell coordinates mismatch")
                    cells.append(dict(cell, directory=relative))
                records.append(record)
                write_json(root / "progress.json", {"records": records, "cells": cells})
    if args.mode == "sentinel":
        if controls["release"]["result"]["status"] != "PASS":
            raise ValueError("sentinel source negative control unavailable")
        expected = json.loads(args.expected.read_text())["sentinel"]
        check_sentinel(cells[0], expected, root)
        controls["input_audio"] = sentinel_controls(cells[0], expected, root)
        write_json(
            root / "sentinel.json",
            {
                "status": "PASS",
                "provenance": provenance,
                "controls": controls,
                "records": records,
                "cells": cells,
            },
        )
        print("PASS: actual-render sentinel and input/audio negative controls")
        return 0
    comparisons = summarize(plan, cells, root)
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "provenance": provenance,
        "plan_sha256": plan_hash(plan),
        "source_commit": plan["source_commit"],
        "controls": controls,
        "records": records,
        "cells": cells,
        "comparisons": comparisons,
        "status": verdict(
            [c["status"] for c in cells]
            + [c["result"]["status"] for c in controls.values()]
            + [c["status"] for c in comparisons if c["kind"] != "cross_runtime"]
        ),
    }
    if args.baseline is not None:
        report["baseline_comparison"] = baseline_drift(plan, cells, root, args.baseline)
    write_json(root / "repeatability-runtime.json", report)
    publish = dict(report)
    publish["raw_report_sha256"] = sha256(
        (root / "repeatability-runtime.json").read_bytes()
    )
    publish["parameter_names"] = next(
        (c["parameter_names"] for c in cells if c["status"] == "PASS"), []
    )
    publish["cells"] = [
        {
            k: v
            for k, v in c.items()
            if k not in ("normalized", "physical", "parameter_names", "inputs")
        }
        for c in cells
    ]
    reference = cells[0]
    publish["sentinel"] = (
        None
        if reference["status"] != "PASS"
        else {
            "input_sha256": reference["input_sha256"],
            "plan_sha256": reference["plan_sha256"],
            "artifact_sha256": {
                k: v["sha256"] for k, v in reference["artifacts"].items()
            },
            "label_byte_hex": reference["label_byte_hex"],
        }
    )
    write_json(root / "publication.json", publish)
    print(report["status"] + ": full matrix recorded")
    return 0 if report["status"] == "PASS" else 1


def baseline_drift(plan, cells, root, baseline, *, historical_runner=None):
    """Audit historical schema-1 receipts without inventing missing profile fields.

    This is a drift comparison, never a legacy path to current qualification.
    The exact historical raw report was pinned before the new measurement.
    """
    raw = (baseline / "repeatability-runtime.json").read_bytes()
    if sha256(raw) != plan["baseline_raw_report_sha256"]:
        raise ValueError("historical raw report hash mismatch")
    old = json.loads(raw)
    old_plan = json.loads((baseline / "preregistered-plan.json").read_text())
    if plan_hash(old_plan) != plan["baseline_plan_sha256"]:
        raise ValueError("historical preregistration mismatch")
    old_cells = {cell_key(c): c for c in old["cells"]}
    if (
        set(old_cells) != {cell_key(c) for c in expected_cells(plan)}
        or len(old["cells"]) != 128
    ):
        raise ValueError("historical matrix completeness mismatch")
    executions = set()
    for record in old["records"]:
        directory = baseline / record["directory"]
        worker = json.loads((directory / "result.json").read_text())
        if (
            record["exit_code"] != 0
            or record["worker"] != {k: v for k, v in worker.items() if k != "cells"}
            or worker["run_id"] != old["run_id"]
            or worker["source_sha256"] != expected_source_hashes()
            or not worker["source_validated_before_import"]
        ):
            raise ValueError("historical worker provenance mismatch")
        executions.add(worker["execution_id"])
        for c in worker["cells"]:
            if dict(c, directory=record["directory"]) != old_cells[cell_key(c)]:
                raise ValueError("historical cell/worker mismatch")
    if len(executions) != 16:
        raise ValueError("historical fresh-process count mismatch")
    comparisons = []
    for cell in cells:
        previous = old_cells[cell_key(cell)]
        case = next(c for c in old_plan["cases"] if c["name"] == cell["case"])
        if (
            previous["status"] != "PASS"
            or previous["identity"] != coordinates(case["index"], cell["batch_size"])
            or previous["inputs"] != case_inputs(old_plan, case)
            or previous["input_sha256"] != sha256(json_bytes(previous["inputs"]))
            or previous["parameter_names"] != plan["parameter_names"]
            or previous["label_byte_hex"] != cell["label_byte_hex"]
            or set(previous["artifacts"]) != set(plan["artifacts"])
        ):
            raise ValueError("historical cell contract mismatch")
        historical = {}
        for name, spec in plan["artifacts"].items():
            record = previous["artifacts"][name]
            if (
                record["samples"] != spec["shape"][0]
                or record["file"] != cell["case"] + "." + name + ".f32le"
            ):
                raise ValueError("historical artifact count/name mismatch")
            historical[name] = read_artifact(baseline / previous["directory"], record)
        for kind in ("normalized", "physical"):
            if historical[kind] != struct.pack(
                "<78f", *[previous[kind][n] for n in plan["parameter_names"]]
            ):
                raise ValueError("historical parameter map mismatch")
        if cell["status"] != "PASS":
            comparisons.append({"cell": list(cell_key(cell)), "status": "NO_VERDICT"})
            continue
        actual = validate_cell(plan, cell, root, historical_runner=historical_runner)
        metrics = {
            name: compare_bytes(historical[name], actual[name])
            for name in plan["artifacts"]
        }
        comparisons.append(
            {
                "cell": list(cell_key(cell)),
                "status": "PASS"
                if all(m["equal_bytes"] for m in metrics.values())
                else "FAIL",
                "artifacts": metrics,
            }
        )
    return {
        "historical_raw_report_sha256": sha256(raw),
        "historical_plan_sha256": plan_hash(old_plan),
        "historical_profile_environment": "not recorded by schema 1; not retroactively asserted",
        "new_profile": plan["profile"],
        "comparisons": comparisons,
    }


def sentinel_controls(cell, expected, root):
    controls = {}
    for field, message in (
        ("input_sha256", "sentinel input mismatch"),
        ("artifact_sha256", "sentinel artifact hash mismatch"),
    ):
        mutated = json.loads(json.dumps(expected))
        if field == "input_sha256":
            mutated[field] = "0" * 64
        else:
            mutated[field]["audio"] = "0" * 64
        try:
            check_sentinel(cell, mutated, root)
        except ValueError as exc:
            if str(exc) != message:
                raise
            controls[field] = {"status": "PASS", "rejected_reason": str(exc)}
        else:
            raise ValueError("negative control accepted mutation")
    if root is not None:
        directory = root / cell["directory"]
        original = read_artifact(directory, cell["artifacts"]["audio"])
        changed = bytes([original[0] ^ 1]) + original[1:]
        # Mutate an actual copied raw file; preserve the genuine measurement.
        temporary = tempfile.TemporaryDirectory()
        copied_root = Path(temporary.name)
        shutil.copyfile(root / "provenance.json", copied_root / "provenance.json")
        shutil.copytree(directory, copied_root / cell["directory"])
        (
            copied_root / cell["directory"] / cell["artifacts"]["audio"]["file"]
        ).write_bytes(changed)
        try:
            check_sentinel(cell, expected, copied_root)
        except ValueError as exc:
            if str(exc) != "artifact hash mismatch":
                raise
            controls["actual_audio_byte_flip"] = {
                "status": "PASS",
                "rejected_reason": str(exc),
                "metrics": compare_bytes(original, changed),
            }
        else:
            raise ValueError("actual audio mutation accepted")
        finally:
            temporary.cleanup()
        mutated_cell = json.loads(json.dumps(cell))
        mutated_cell["inputs"]["index"] += 1
        mutated_cell["input_sha256"] = sha256(json_bytes(mutated_cell["inputs"]))
        try:
            check_sentinel(mutated_cell, expected, root)
        except ValueError as exc:
            if str(exc) != "cell input/configuration mismatch":
                raise
            controls["actual_input_change"] = {
                "status": "PASS",
                "rejected_reason": str(exc),
            }
        else:
            raise ValueError("actual input mutation accepted")
    return controls


def source_negative(source):
    with tempfile.TemporaryDirectory() as directory:
        altered = Path(directory) / "source"
        shutil.copytree(
            source, altered, ignore=shutil.ignore_patterns(".git", "__pycache__")
        )
        path = altered / "torchsynth/config.py"
        path.write_bytes(path.read_bytes() + b"\n# deliberate source mutation\n")
        result = subprocess.run(
            [
                sys.executable,
                __file__,
                "worker",
                "--preflight-only",
                "--source-root",
                str(altered),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(result.stdout)
        if (
            result.returncode != 1
            or report["error"]
            != "ValueError: source hash mismatch: torchsynth/config.py"
            or report["torch_imported"]
        ):
            raise ValueError("source mutation was not rejected before Torch import")
        return {"status": "PASS", "rejection": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("matrix", "sentinel", "worker", "negative"))
    parser.add_argument("--output", type=Path, default=REPO / "out/repeatability")
    parser.add_argument("--source-root", type=Path, default=Path("/opt/torchsynth"))
    parser.add_argument("--current-python", default=sys.executable)
    parser.add_argument(
        "--image", help="exact built image ID (provided by the shell wrapper)"
    )
    parser.add_argument("--runtime", choices=("release", "current"), default="release")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--run-id", default="standalone")
    parser.add_argument(
        "--baseline",
        type=Path,
        help="preserved schema-1 raw matrix to audit and compare",
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--sentinel", action="store_true")
    parser.add_argument(
        "--expected",
        type=Path,
        default=REPO / "sim/reference/repeatability-runtime.json",
    )
    args = parser.parse_args()
    if args.mode in ("matrix", "sentinel"):
        if not args.image:
            parser.error(
                "--image is required; use qualify_repeatability.sh to build the locked image"
            )
        return run(args)
    stdout, stderr = io.StringIO(), io.StringIO()
    code = 0
    with (
        warnings.catch_warnings(record=True) as caught,
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        warnings.simplefilter("always")
        try:
            report = (
                source_negative(args.source_root)
                if args.mode == "negative"
                else worker(args)
            )
        except Exception as exc:  # noqa: BLE001 -- Process boundary reports import/resource failures.
            report = {
                "status": "NO_VERDICT",
                "error": type(exc).__name__ + ": " + str(exc),
                "torch_imported": "torch" in sys.modules,
            }
            code = 1
    report.update(
        warnings=[str(w.message) for w in caught],
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )
    if args.mode == "worker" and not args.preflight_only and "cells" in report:
        write_json(args.output / "result.json", report)
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
