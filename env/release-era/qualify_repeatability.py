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
from pathlib import Path

from probe import json_bytes, sha256, validate_source

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PLAN = HERE / "repeatability-matrix.json"
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


def validate_results(plan, cells):
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
        }
    for kind, values in (("normalized", normalized), ("physical", physical)):
        data = struct.pack("<78f", *[values[k] for k in sorted(values)])
        if not all(math.isfinite(v) for v in values.values()):
            raise ValueError("nonfinite parameter")
        path = output["directory"] / (case["name"] + "." + kind + ".f32le")
        path.write_bytes(data)
        artifacts[kind] = {"file": path.name, "sha256": sha256(data), "samples": 78}
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


def pair(left, right, root):
    if left["status"] != "PASS" or right["status"] != "PASS":
        return {
            "status": "NO_VERDICT",
            "reason": "one or both render cells unavailable",
        }
    compared = {}
    for name in left["artifacts"]:
        a = read_artifact(root / left["directory"], left["artifacts"][name])
        b = read_artifact(root / right["directory"], right["artifacts"][name])
        compared[name] = compare_bytes(a, b)
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


def summarize(plan, cells, root):
    validate_results(plan, cells)
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
                            **pair(lookup[a], lookup[b], root),
                        )
                    )
    return comparisons


def check_sentinel(cell, expected):
    if cell["status"] != "PASS":
        raise ValueError("sentinel render unavailable: " + cell.get("error", "unknown"))
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


def invoke(command, timeout=1800):
    """Unavailable executables and timeouts are results, never missing cells."""
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=dict(os.environ, **THREAD_ENV, PYTHONDONTWRITEBYTECODE="1"),
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
            "--memory",
            "6g",
            "--cpus",
            "1",
            "--mount",
            "type=bind,src=" + str(REPO) + ",dst=/repo,readonly",
            "--mount",
            "type=bind,src=" + str(directory) + ",dst=/output",
            "--entrypoint",
            "python",
            args.image,
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
    }
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
                    negative = invoke(negative_command, timeout=120)
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
                result = invoke(command)
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
                observed = {c["case"]: c for c in data.get("cells", [])}
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
                    cells.append(dict(cell, directory=relative))
                records.append(record)
                write_json(root / "progress.json", {"records": records, "cells": cells})
    if args.mode == "sentinel":
        if controls["release"]["result"]["status"] != "PASS":
            raise ValueError("sentinel source negative control unavailable")
        expected = json.loads(args.expected.read_text())["sentinel"]
        check_sentinel(cells[0], expected)
        controls["input_audio"] = sentinel_controls(
            cells[0], expected, root / cells[0]["directory"]
        )
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


def sentinel_controls(cell, expected, directory=None):
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
            check_sentinel(cell, mutated)
        except ValueError as exc:
            if str(exc) != message:
                raise
            controls[field] = {"status": "PASS", "rejected_reason": str(exc)}
        else:
            raise ValueError("negative control accepted mutation")
    if directory is not None:
        original = read_artifact(directory, cell["artifacts"]["audio"])
        changed = bytes([original[0] ^ 1]) + original[1:]
        mutated_cell = json.loads(json.dumps(cell))
        mutated_cell["artifacts"]["audio"]["sha256"] = sha256(changed)
        try:
            check_sentinel(mutated_cell, expected)
        except ValueError as exc:
            if str(exc) != "sentinel artifact hash mismatch":
                raise
            controls["actual_audio_byte_flip"] = {
                "status": "PASS",
                "rejected_reason": str(exc),
                "metrics": compare_bytes(original, changed),
            }
        else:
            raise ValueError("actual audio mutation accepted")
        mutated_cell = json.loads(json.dumps(cell))
        mutated_cell["inputs"]["index"] += 1
        mutated_cell["input_sha256"] = sha256(json_bytes(mutated_cell["inputs"]))
        try:
            check_sentinel(mutated_cell, expected)
        except ValueError as exc:
            if str(exc) != "sentinel input mismatch":
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
