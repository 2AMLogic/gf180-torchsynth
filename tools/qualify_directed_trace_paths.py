"""Directed per-path trace-capture measurement and receipt checks (issue #286).

Default invocation performs the DR-0006 host-gated release-era run of
``env/release-era/capture_directed_trace_paths.py`` for the preregistered plan
derived from ``spec/reference/directed-coverage-v1.json``. On a host outside
the recorded measured scope (the same Apple M5 / macOS / Docker tuple the #23
launcher requires; the sanctioned AWS box is NOT presumed admitted) it runs
nothing and writes an explicit ``UNRUN`` receipt. ``UNRUN`` is never a pass.

On an admitted host it rebuilds the unchanged release image, runs the worker
offline into a fresh raw-output directory, rehashes every retained payload,
derives all activation measurements from those bytes, verifies the result and
writes the bounded receipt ``sim/reference/directed-trace-paths-v1.json``.
Existing publications are never touched; an existing receipt is replaced only
when it is itself ``UNRUN``.

``--check-inputs`` (stdlib, any CI host) verifies pins and the derived plan.
``--check-receipt`` verifies a committed receipt: exit 0 only for a verified
PASS, exit 2 for UNRUN or absent (never a pass), exit 1 otherwise.
``--raw`` supplies the retained payload directory for measured receipts.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PUBLICATION_PATH = ROOT / "sim/reference/directed-trace-paths-v1.json"
WORKER_PATH = ROOT / "env/release-era/capture_directed_trace_paths.py"
COVERAGE_SHA256 = "b181bc4b956eba7334b24111ac6ca54188b45923019a9586ecb04c8e9726c5bc"
EXISTING_PUBLICATIONS = (
    "sim/reference/trace-capture.json",
    "sim/reference/trace-registry-prototype.json",
)


def load_by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_modules():
    registry = load_by_path("trace_registry", ROOT / "src/torchsynth_voice/trace_registry.py")
    load_by_path("trace_capture", ROOT / "src/torchsynth_voice/trace_capture.py")
    paths = load_by_path(
        "directed_trace_paths", ROOT / "src/torchsynth_voice/directed_trace_paths.py"
    )
    return registry, paths


def load_inputs(registry):
    return (
        registry.load_registry(),
        json.loads(paths_coverage().read_bytes()),
        json.loads((ROOT / "spec/reference/directed-voice-v1.json").read_bytes()),
    )


def paths_coverage():
    return ROOT / "spec/reference/directed-coverage-v1.json"


def sha(path):
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def input_hashes(registry, paths):
    return {
        "registry_sha256": sha(registry.REGISTRY_PATH),
        "coverage_sha256": sha(paths.COVERAGE_PATH),
        "directed_sha256": sha(paths.DIRECTED_PATH),
        "upstream_sha256": sha(ROOT / "spec/reference/upstream.json"),
        "parameter_inventory_sha256": sha(ROOT / "spec/reference/parameter-inventory-v1.json"),
        "worker_sha256": sha(WORKER_PATH),
        "producer_sha256": sha(Path(__file__)),
        "shared_module_sha256": sha(paths.__file__),
        "capture_module_sha256": sha(ROOT / "src/torchsynth_voice/trace_capture.py"),
        "existing_publications_sha256": {
            name: sha(ROOT / name) for name in EXISTING_PUBLICATIONS
        },
    }


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def host_gate():
    """(admitted, observed, reason). Mirrors the #23 launcher's measured tuple."""
    observed = {
        "system": platform.system(),
        "machine": platform.machine(),
        "platform": platform.platform(),
    }
    if observed["system"] != "Darwin" or observed["machine"] != "arm64":
        return False, observed, (
            "host outside DR-0006 measured scope: requires Darwin arm64 "
            "(Apple M5 / macOS 26.5.1 / Docker linux/arm64 29.7.2); observed "
            + observed["system"]
            + " "
            + observed["machine"]
        )
    try:
        observed["cpu"] = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip()
        observed["os_version"] = subprocess.check_output(
            ["sw_vers", "-productVersion"], text=True
        ).strip()
        observed["docker_server"] = subprocess.check_output(
            ["docker", "version", "--format",
             "{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}"],
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        return False, observed, "host identity probe failed: " + str(error)
    if (
        observed["cpu"] != "Apple M5"
        or observed["os_version"] != "26.5.1"
        or observed["docker_server"] != "linux/arm64 29.7.2"
    ):
        return False, observed, "host outside DR-0006 measured scope"
    return True, observed, "admitted"


def check_inputs_mode(registry, paths):
    document, coverage, directed = load_inputs(registry)
    require = registry.require
    require(sha(paths.COVERAGE_PATH) == COVERAGE_SHA256, "stale coverage pin")
    plan = paths.build_plan(document, coverage, directed)
    for name in EXISTING_PUBLICATIONS:
        require((ROOT / name).exists(), "existing publication absent: " + name)
    print(
        json.dumps(
            {
                "status": "PASS",
                "registry_token": registry.registry_token(),
                "traces": len({r["trace"] for r in plan["rows"]}),
                "rows": len(plan["rows"]),
                "cases": [c["id"] for c in plan["cases"]],
            }
        )
    )


def check_receipt_mode(registry, paths, args):
    if not args.publication.exists():
        print("receipt: ABSENT - absence is never reported as a pass")
        raise SystemExit(2)
    document, coverage, directed = load_inputs(registry)
    receipt = json.loads(args.publication.read_bytes())
    status = paths.verify_receipt(receipt, document, coverage, directed, args.raw)
    summary = receipt["summary"]
    print(json.dumps({"status": status, "summary": summary}))
    if status == "UNRUN":
        print("receipt: UNRUN - measurement has not been executed; not a pass")
        raise SystemExit(2)
    if status != "PASS":
        raise SystemExit(1)


def replaceable(path):
    if not path.exists():
        return True
    try:
        return json.loads(path.read_bytes()).get("status") == "UNRUN"
    except ValueError:
        return False


def host_run(registry, paths, args):
    document, coverage, directed = load_inputs(registry)
    require = registry.require
    require(sha(paths.COVERAGE_PATH) == COVERAGE_SHA256, "stale coverage pin")
    plan = paths.build_plan(document, coverage, directed)
    hashes = input_hashes(registry, paths)
    command_line = [sys.executable, str(Path(__file__).relative_to(ROOT))] + sys.argv[1:]
    require(
        replaceable(args.publication),
        "refusing to overwrite a retained non-UNRUN receipt: " + str(args.publication),
    )
    admitted, observed, reason = host_gate()
    if not admitted:
        receipt = paths.unrun_receipt(plan, document, hashes, reason, observed, command_line)
        paths.verify_receipt(receipt, document, coverage, directed)
        write_json(args.publication, receipt)
        print(json.dumps({"status": "UNRUN", "reason": reason,
                          "receipt": str(args.publication)}))
        raise SystemExit(2)

    sys.path.insert(0, str(ROOT / "src"))
    from torchsynth_voice.artifact_renderer import (
        dispatch_flags,
        dispatch_unset_flags,
        release_profile_environment,
    )

    output = args.output.resolve()
    require(not output.exists(), "output directory already exists; preserve earlier runs")
    output.mkdir(parents=True)
    build = [
        "docker", "build", "--platform", "linux/amd64",
        "--iidfile", str(output / "image-id"),
        "-f", str(ROOT / "env/release-era/Dockerfile"), str(ROOT),
    ]
    with (output / "build.log").open("w") as log:
        subprocess.run(build, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    image = (output / "image-id").read_text().strip()
    image_info = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
    require(
        image_info["Architecture"] == "amd64" and image_info["Os"] == "linux",
        "wrong image platform",
    )
    command = [
        "docker", "run", "--rm", "--pull", "never", "--platform", "linux/amd64",
        "--network", "none", "--memory", "6g", "--cpus", "1",
    ]
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        command += ["--env", name + "=1"]
    profile = release_profile_environment()
    command += [
        *dispatch_flags(profile),
        "--mount", "type=bind,src=" + str(ROOT) + ",dst=/repo,readonly",
        "--mount", "type=bind,src=" + str(output) + ",dst=/output",
        "--entrypoint", "env", image, *dispatch_unset_flags(profile),
        "python", "/repo/env/release-era/capture_directed_trace_paths.py",
        "--worker", "--output", "/output",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    (output / "worker.stdout").write_text(result.stdout)
    (output / "worker.stderr").write_text(result.stderr)
    require(result.returncode == 0, "worker failed; retained stdout/stderr in " + str(output))
    report = json.loads((output / "worker.json").read_bytes())
    require(report["status"] == "WORKER-COMPLETE", "worker did not complete")
    require(report["registry_token"] == registry.registry_token(), "worker registry drift")
    rows, case_checks = paths.rows_from_worker(plan, document, report, output)
    execution = {
        "admitted": True,
        "outcome": "executed",
        "host": observed,
        "command": command,
        "build_command": build,
        "exit_code": result.returncode,
        "raw_output_directory": str(output),
        "execution_id": report["process"]["execution_id"],
        "image_id": image,
        "image_layers": image_info["RootFS"]["Layers"],
    }
    extra = {
        "runtime": report["runtime"],
        "runtime_sha256": report["runtime_sha256"],
        "source_sha256": report["source_sha256"],
        "configuration": report["configuration"],
        "worker_producer_sha256": report["producer_sha256"],
        "worker_warnings": report["warnings"],
        "worker_stderr": result.stderr,
        "case_render_seconds": {
            c["case"]["id"]: c["render_seconds"] for c in report["cases"]
        },
        "project_git": {
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "dirty": bool(
                subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
            ),
        },
    }
    receipt = paths.measured_receipt(plan, rows, case_checks, hashes, execution, extra)
    verified = paths.verify_receipt(receipt, document, coverage, directed, output)
    require(verified == receipt["status"], "verifier disagrees with receipt status")
    write_json(args.publication, receipt)
    print(json.dumps({"status": receipt["status"], "summary": receipt["summary"],
                      "receipt": str(args.publication)}))
    if receipt["status"] != "PASS":
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-inputs", action="store_true")
    parser.add_argument("--check-receipt", action="store_true")
    parser.add_argument("--raw", type=Path, default=None,
                        help="retained raw payload directory for --check-receipt")
    parser.add_argument("--publication", type=Path, default=PUBLICATION_PATH)
    parser.add_argument("--output", type=Path, default=ROOT / "out/directed-trace-paths-v1",
                        help="fresh raw-output directory for the host-gated run")
    args = parser.parse_args()
    registry, paths = load_modules()
    if args.check_inputs:
        check_inputs_mode(registry, paths)
    elif args.check_receipt:
        check_receipt_mode(registry, paths, args)
    else:
        host_run(registry, paths, args)


if __name__ == "__main__":
    main()
