"""Qualified-runtime trace-capture publication and stdlib invariant checks.

Default invocation performs the DR-0006 host-gated release-era qualification:
it rebuilds the unchanged release image, runs the Python 3.9 worker
``env/release-era/capture_traces.py`` offline inside it, verifies every raw
artifact after the worker exits, and writes the bounded publication
``sim/reference/trace-capture.json``. It never reuses an existing output
directory.

``--check-inputs`` is the stdlib-only mode for ordinary CI hosts: it re-verifies
the preregistered input pins, selection plan, directed case existence and both
registry- and capture-level negative controls without Torch or Docker.

``--check-publication`` strictly validates the committed publication against
the current registry identity; it fails when the evidence is stale or absent.
Absence is reported as absent, never as a pass.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_PATH = ROOT / "sim/reference/trace-capture.json"
WORKER_PATH = ROOT / "env/release-era/capture_traces.py"


def load_production_modules():
    """Import the stdlib-only production modules by file path."""
    registry_spec = importlib.util.spec_from_file_location(
        "trace_registry", ROOT / "src/torchsynth_voice/trace_registry.py"
    )
    registry = importlib.util.module_from_spec(registry_spec)
    sys.modules["trace_registry"] = registry
    registry_spec.loader.exec_module(registry)
    capture_spec = importlib.util.spec_from_file_location(
        "trace_capture", ROOT / "src/torchsynth_voice/trace_capture.py"
    )
    capture = importlib.util.module_from_spec(capture_spec)
    sys.modules["trace_capture"] = capture
    capture_spec.loader.exec_module(capture)
    return registry, capture


def load_worker_constants():
    spec = importlib.util.spec_from_file_location(
        "capture_worker_constants", WORKER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def check_inputs_mode(registry, capture, document, constants):
    failures = []

    def require(condition, message):
        if not condition:
            failures.append(message)

    for name, expected in constants.INPUT_SHA256.items():
        actual = sha256((ROOT / name).read_bytes())
        require(
            actual == expected,
            "stale preregistered input: " + name + " (" + actual + ")",
        )
    selection = capture.requested_plan(document, constants.SUBSET)
    require(
        [trace["name"] for trace in selection] == list(constants.SUBSET),
        "selection plan is not a valid graph-ordered subset",
    )
    directed = json.loads(
        (ROOT / "spec/reference/directed-voice-v1.json").read_text()
    )
    directed_ids = {case["id"] for case in directed["cases"]}
    for case in constants.CASES:
        if "directed" in case:
            require(
                case["directed"] in directed_ids,
                "directed case missing: " + case["directed"],
            )
    negative = capture.negative_controls(document)
    require(
        all(entry["status"] == "rejected" for entry in negative.values())
        and len(negative) >= 10,
        "negative controls did not all reject",
    )
    endpoints = capture.endpoint_pairs(document)
    require(len(endpoints) == 5, "endpoint pair coverage drift")
    if failures:
        for failure in failures:
            print("FAIL:", failure)
        raise SystemExit(1)
    print(
        json.dumps(
            {
                "status": "PASS",
                "registry_token": registry.registry_token(),
                "cases": len(constants.CASES),
                "subset": list(constants.SUBSET),
                "negative_controls": sorted(negative),
                "endpoint_pairs": len(endpoints),
            }
        )
    )


def check_publication_mode(registry, capture, document, constants):
    if not PUBLICATION_PATH.exists():
        print(
            "publication: ABSENT — requires the host-gated qualified release-era "
            "run; absence is never reported as a pass"
        )
        raise SystemExit(2)
    publication = json.loads(PUBLICATION_PATH.read_bytes())
    require = registry.require
    require(publication["status"] == "PASS", "publication did not pass")
    require(
        publication["capture_version"] == "trace-capture-v1",
        "unknown capture version",
    )
    require(
        publication["registry_token"] == registry.registry_token(),
        "publication registry identity is stale",
    )
    require(
        publication["registry_sha256"] == sha256(registry.REGISTRY_PATH.read_bytes()),
        "publication registry bytes are stale",
    )
    require(
        publication["runtime_profile"] == "release-mkl-compatible-v1",
        "publication is not from the qualified runtime profile",
    )
    require(
        publication["subset"] == list(constants.SUBSET),
        "publication selection drifted",
    )
    require(
        [case["case"]["id"] for case in publication["cases"]]
        == [case["id"] for case in constants.CASES],
        "publication case set/order drifted",
    )
    for case in publication["cases"]:
        label = case["case"]["id"]
        require(case["passive_capture_equal_bytes"] is True, label + ": not equal")
        capture.validate_selected_capture(document, None, case["full_inventory"], 32)
        capture.validate_selected_capture(
            document, publication["subset"], case["partial_inventory"], 32
        )
        for route, result in case["upsampling_endpoint_checks"].items():
            require(result["status"] == "PASS", label + ": endpoint loss " + route)
        require(
            case["normalization_branch"]["output_equals_original_branch_bytes"]
            is True,
            label + ": normalization branch evidence missing",
        )
        for mode, cost in case["modes"].items():
            require(
                cost["render_seconds"] >= 0.0 and cost["retained_capture_bytes"] >= 0,
                label + ": malformed measured cost " + mode,
            )
    require(
        all(
            entry["status"] == "rejected"
            for entry in publication["negative_controls"].values()
        ),
        "publication negative controls incomplete",
    )
    global_case = publication["cases"][0]
    require(
        global_case["case"]["id"] == "global-0"
        and global_case["prototype_sentinel_comparison"]["status"] == "PASS",
        "global-0 prototype sentinel comparison missing",
    )
    require(
        publication["normalization_exercise"]
        and len(publication["normalization_exercise"]) >= 5,
        "normalization exercise incomplete",
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(PUBLICATION_PATH),
                "registry_token": publication["registry_token"],
                "cases": len(publication["cases"]),
            }
        )
    )


def host_run(args):
    """DR-0006 gated qualification; writes the bounded publication."""
    require = (load_production_modules()[0]).require
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("unqualified host")
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
        "output directory already exists; preserve earlier experiments",
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
        subprocess.run(build, check=True, stdout=log, stderr=subprocess.STDOUT,
                       timeout=1800)
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
        "/repo/env/release-era/capture_traces.py",
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
    registry, capture = load_production_modules()
    document = registry.load_registry()
    constants = load_worker_constants()
    report = json.loads((output / "worker.json").read_bytes())
    require(report["status"] == "PASS", "worker did not pass")

    def require_file(path, digest, size):
        data = path.read_bytes()
        require(
            sha256(data) == digest and (size is None or len(data) == size),
            "raw artifact integrity failure: " + path.name,
        )

    for case in report["cases"]:
        label = case["case"]["id"]
        capture.validate_selected_capture(
            document, None, case["full_inventory"], 32
        )
        capture.validate_selected_capture(
            document, report["subset"], case["partial_inventory"], 32
        )
        for mode in ("uncaptured", "partial", "full"):
            require_file(
                output / (label + "." + mode + ".batch-audio.f32le"),
                case["modes"][mode]["batch_audio_sha256"],
                32 * 176400 * 4,
            )
        for entry in case["full_inventory"] + case["partial_inventory"]:
            require_file(
                output / entry["file"], entry["sha256"], entry["size_bytes"]
            )
    require(
        all(entry["status"] == "rejected" for entry in
            report["negative_controls"].values()),
        "worker negative controls incomplete",
    )
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
        "build_log_sha256": sha256((output / "build.log").read_bytes()),
        "image_id": image,
        "image_layers": image_info["RootFS"]["Layers"],
        "build_warnings": [
            line
            for line in (output / "build.log").read_text().splitlines()
            if "warning" in line.lower()
        ],
    }
    diff = subprocess.check_output(
        ["git", "diff", "--binary", "HEAD"], cwd=ROOT
    )
    untracked = (
        subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    untracked_files = {
        name: sha256((ROOT / name).read_bytes()) for name in untracked if name
    }
    report["project_git"] = {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dirty": bool(diff or untracked_files),
        "diff_sha256": sha256(diff),
        "untracked_files_sha256": untracked_files,
        "untracked_state_sha256": sha256(json_bytes(untracked_files)),
    }
    write_json(PUBLICATION_PATH, report)
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(PUBLICATION_PATH),
                "cases": len(report["cases"]),
                "registry_token": report["registry_token"],
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-inputs", action="store_true")
    parser.add_argument("--check-publication", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "out/trace-capture",
        help="fresh output directory for the host-gated qualification run",
    )
    args = parser.parse_args()
    registry, capture = load_production_modules()
    document = registry.load_registry()
    constants = load_worker_constants()
    if args.check_inputs:
        check_inputs_mode(registry, capture, document, constants)
        return
    if args.check_publication:
        check_publication_mode(registry, capture, document, constants)
        return
    host_run(args)


if __name__ == "__main__":
    main()
