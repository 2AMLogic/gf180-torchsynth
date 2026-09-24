"""The v1 artifact adapter. DSP runs in a separately admitted Python 3.9 process.

The public request/observation seam permits same-render capture providers; see
spec/CORPUS-RUNNER.md. Importing this module needs only the standard library.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import platform
import stat
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .artifacts import (
    ValidationError,
    canonical_bytes,
    content_id,
    loads,
    validate_artifact,
)
from .contract import UpstreamContract, repository_root, sha256_file
from .identity import SoundIdentity
from .storage import ArtifactStore, StoredArtifact

PROFILE = "release-mkl-compatible-v1"
QUALIFICATION_SHA256 = (
    "611fe2121330a7a467ffab1deade50e59f6dd0ea25d4da86d129b9b501fa9cc3"
)
RENDERER = "artifact-renderer-v1-release-mkl-compatible-v1"
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


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def json_bytes(value):
    canonical_bytes(value)  # reject non-JSON/nonfinite values before serialization
    return (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture(index):
    sound = SoundIdentity(index)
    batch, slot = sound.batch_coordinates(128)
    return dict(
        sound_index=index,
        upstream_batch_index=batch,
        upstream_slot=slot,
        upstream_name=sound.upstream_name,
        is_train=sound.is_train,
    )


def project_identity(root=None):
    root = Path(root or repository_root())

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    commit = git("rev-parse", "HEAD").decode().strip()
    diff = git("diff", "--binary", "HEAD")
    untracked = {}
    for raw in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if not raw:
            continue
        name = raw.decode("utf-8")
        path = root / name
        require(stat.S_ISREG(path.lstat().st_mode), "nonregular project input")
        untracked[name] = sha256_file(path)
    return dict(
        commit=commit,
        dirty=bool(diff or untracked),
        diff_sha256=digest(diff),
        untracked_sha256=digest(canonical_bytes(untracked)),
    )


def qualification():
    data = (repository_root() / "sim/reference/repeatability-runtime.json").read_bytes()
    require(
        digest(data) == QUALIFICATION_SHA256, "ratified runtime publication changed"
    )
    record = loads(data)
    runtime = next(
        r["worker"]["runtime"]
        for r in record["records"]
        if r["directory"] == "release-32-1"
    )
    return record, runtime


def runtime_descriptor(runtime):
    # Full CPU text is retained in the run receipt; v1 permits restricted strings.
    return dict(
        lock_sha256=runtime["lock_sha256"],
        os=runtime["platform"],
        architecture=runtime["machine"],
        cpu="sha256-" + digest(runtime["cpu"].encode()),
        device="cpu",
        versions={
            **{k: runtime[k] for k in ("python", "torch", "lightning", "numpy")},
            "torchsynth": "source-2b0964d4c6c3d472a2a0d54d91b408caaeffca6d",
        },
    )


def request_template(
    *,
    project_root=None,
    batch_size=32,
    locks=None,
    trace_registry_version="audio-only-v1",
    requested_traces=(),
):
    contract = UpstreamContract.load()
    _, runtime = qualification()
    return dict(
        profile=dict(
            name=contract.data["profile"],
            contract_sha256=sha256_file(repository_root() / "spec/VOICE-CONTRACT.md"),
            sample_rate=44100,
            control_rate=441,
            duration_seconds=4,
            expected_sample_count=176400,
            channels=1,
            dtype="float32",
            nebula="default",
            normalization="conditional-whole-clip-peak",
        ),
        source=dict(
            commit=contract.target_commit,
            manifest_sha256=sha256_file(contract.manifest_path),
            files=contract.files,
        ),
        runtime=runtime_descriptor(runtime),
        project_git=project_identity(project_root),
        renderer_version=RENDERER,
        execution=dict(
            mode="canonical-batch", batch_size=batch_size, reproducible=True
        ),
        locks_physical=dict(locks or {}),
        trace_registry_version=trace_registry_version,
        requested_traces=list(requested_traces),
    )


def validate_request(request):
    keys = {
        "profile",
        "source",
        "runtime",
        "project_git",
        "renderer_version",
        "fixture",
        "execution",
        "locks_physical",
        "trace_registry_version",
        "requested_traces",
    }
    require(type(request) is dict and set(request) == keys, "request fields mismatch")
    contract = UpstreamContract.load()
    require(
        request["source"]
        == dict(
            commit=contract.target_commit,
            manifest_sha256=sha256_file(contract.manifest_path),
            files=contract.files,
        ),
        "source pin mismatch",
    )
    _, runtime = qualification()
    require(
        request["runtime"] == runtime_descriptor(runtime),
        "runtime outside ratified profile",
    )
    require(request["renderer_version"] == RENDERER, "renderer profile mismatch")
    require(
        request["execution"]
        in [
            dict(mode="canonical-batch", batch_size=n, reproducible=True) for n in (32,)
        ],
        "unqualified execution configuration",
    )
    require(
        request["fixture"] == fixture(request["fixture"]["sound_index"]),
        "fixture mismatch",
    )
    inventory = loads(
        (repository_root() / "spec/reference/parameter-inventory-v1.json").read_bytes()
    )
    parameters = {p["name"]: p for p in inventory["parameters"]}
    require(type(request["locks_physical"]) is dict, "locks must be named")
    for name, value in request["locks_physical"].items():
        require(name in parameters, "unknown parameter lock")
        require(
            type(value) in (float, int) and math.isfinite(value),
            "invalid physical lock",
        )
        require(
            parameters[name]["minimum"] <= value <= parameters[name]["maximum"],
            "lock outside range",
        )
    # Reuse the public complete-artifact validator for all closed input shapes.
    probe = _metadata(
        request,
        dict(
            normalized_by_name=dict.fromkeys(parameters, 0.5),
            physical_by_name={
                k: request["locks_physical"].get(k, 0.5) for k in parameters
            },
            locks_physical=request["locks_physical"],
        ),
        "0" * 64,
        bytes(176400 * 4),
        1,
        {
            name: dict(ref=f"traces/{i}.bin", sha256="0" * 64, size_bytes=0)
            for i, name in enumerate(request["requested_traces"])
        },
    )
    validate_artifact(probe)


def validate_binding(record, request):
    validate_request(request)
    validate_artifact(record)
    inputs = record["inputs"]["value"]
    for key in request.keys() - {"locks_physical"}:
        require(inputs[key] == request[key], "artifact request mismatch: " + key)
    require(
        inputs["parameters"]["locks_physical"] == request["locks_physical"],
        "patch lock mismatch",
    )
    names = {
        p["name"]
        for p in loads(
            (
                repository_root() / "spec/reference/parameter-inventory-v1.json"
            ).read_bytes()
        )["parameters"]
    }
    require(
        set(inputs["parameters"]["normalized_by_name"]) == names,
        "parameter inventory mismatch",
    )


def samples(data):
    require(
        type(data) is bytes and len(data) == 176400 * 4, "sample byte count mismatch"
    )
    values = [v[0] for v in struct.iter_unpack("<f", data)]
    require(all(math.isfinite(v) for v in values), "nonfinite samples")
    return values


def _metadata(request, parameters, noise_hash, audio, gain, traces):
    values = samples(audio)
    peak_index = max(range(len(values)), key=lambda i: abs(values[i]))
    inputs = copy.deepcopy(request)
    del inputs["locks_physical"]
    inputs.update(
        parameters=parameters,
        noise=dict(
            seed=13, slot=request["fixture"]["sound_index"] % 32, sha256=noise_hash
        ),
    )
    return dict(
        schema="torchsynth-render-artifact",
        schema_version=1,
        status="complete",
        artifact_id=content_id(inputs),
        inputs=dict(state="available", value=inputs),
        audio=dict(
            state="available",
            value=dict(
                file=dict(
                    ref="audio.f32le", sha256=digest(audio), size_bytes=len(audio)
                ),
                encoding="f32le-mono",
                observed_sample_count=len(values),
                peak_abs=abs(values[peak_index]),
                peak_index=peak_index,
                rms=math.sqrt(math.fsum(v * v for v in values) / len(values)),
                dc_mean=math.fsum(values) / len(values),
                clipped_sample_count=sum(abs(v) > 1 for v in values),
                normalization_gain=gain,
            ),
        ),
        traces=dict(state="available", value=traces),
        warnings=[],
        failures=[],
    )


@dataclass(frozen=True)
class RenderProduct:
    stored: StoredArtifact
    receipt: dict

    @property
    def reference(self):
        return self.stored.reference


def render_artifact(request, store: ArtifactStore, backend, *, capture_provider=None):
    """Validate a request before backend access, then publish an immutable artifact.

    backend(request, capture_provider=...) returns audio/noise/pre-normalization
    bytes, parameter maps, selected noise slot, traces and an execution receipt.
    A capture provider is attached by the backend during this original render.
    """
    validate_request(request)
    observed = backend(copy.deepcopy(request), capture_provider=capture_provider)
    require(
        set(observed)
        == {
            "audio",
            "noise",
            "pre_normalization",
            "parameters",
            "noise_slot",
            "noise_sha256",
            "traces",
            "receipt",
        },
        "worker observation fields mismatch",
    )
    samples(observed["noise"])
    require(
        observed["noise_slot"] == request["fixture"]["sound_index"] % 32,
        "noise slot mismatch",
    )
    require(
        observed["noise_sha256"] == digest(observed["noise"]), "noise hash mismatch"
    )
    pre_peak = max(map(abs, samples(observed["pre_normalization"])))
    gain = 1 / pre_peak if pre_peak > 1 else 1.0
    require(
        set(observed["traces"]) == set(request["requested_traces"]),
        "captured trace names mismatch",
    )
    payloads = {"audio.f32le": observed["audio"]}
    traces = {}
    for index, name in enumerate(request["requested_traces"]):
        data = observed["traces"][name]
        require(type(data) is bytes, "trace payload must be bytes")
        path = f"traces/trace-{index}.bin"
        payloads[path] = data
        traces[name] = dict(ref=path, sha256=digest(data), size_bytes=len(data))
    record = _metadata(
        request,
        observed["parameters"],
        observed["noise_sha256"],
        observed["audio"],
        gain,
        traces,
    )
    record["warnings"] = sorted(set(observed["receipt"].get("warning_categories", [])))
    validate_binding(record, request)
    with tempfile.TemporaryDirectory(dir=store.root / ".staging") as temporary:
        stage = Path(temporary)
        for name, data in payloads.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (stage / "metadata.json").write_bytes(json_bytes(record))
        stored = store.publish(stage)
    receipt = copy.deepcopy(observed["receipt"])
    receipt["observations"] = dict(
        pre_normalization_peak=pre_peak,
        pre_normalization_sha256=digest(observed["pre_normalization"]),
        normalization_gain_derived=gain,
        richer_module_facts=dict(state="unavailable", reason="audio-only-adapter"),
    )
    return RenderProduct(stored, receipt)


class DockerBackend:
    """Qualified emulated-host launch; construction performs no render/data access."""

    def __init__(self, *, project_root=None):
        self.project_root = Path(project_root or repository_root()).resolve()

    def __call__(self, request, *, capture_provider=None):
        validate_request(request)
        require(
            capture_provider is None,
            "Docker capture provider integration belongs to issue 24",
        )
        require(
            not request["project_git"]["dirty"],
            "production requires a frozen clean producer checkout",
        )
        require(
            project_identity(self.project_root) == request["project_git"],
            "producer checkout changed",
        )
        publication, _ = qualification()
        host = dict(
            host_platform=platform.platform(),
            host_os_version=platform.mac_ver()[0],
            host_architecture=platform.machine(),
            host_cpu=subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip(),
            docker_server=subprocess.check_output(
                [
                    "docker",
                    "version",
                    "--format",
                    "{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}",
                ],
                text=True,
            ).strip(),
        )
        require(
            platform.system() == "Darwin"
            and host["host_os_version"] == "26.5.1"
            and host["host_architecture"] == "arm64"
            and host["host_cpu"] == "Apple M5"
            and host["docker_server"] == publication["provenance"]["docker_server"],
            "host outside DR-0006 measured scope",
        )
        image = publication["provenance"]["image"]["Id"]
        inspection = loads(
            subprocess.check_output(["docker", "image", "inspect", image])
        )[0]
        require(
            inspection["Id"] == image
            and inspection["Architecture"] == "amd64"
            and inspection["Os"] == "linux",
            "qualified image mismatch",
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            (directory / "request.json").write_bytes(json_bytes(request))
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
                *[
                    part
                    for k, v in THREAD_ENV.items()
                    for part in ("--env", k + "=" + v)
                ],
                "--env",
                "MKL_CBWR=COMPATIBLE",
                "--mount",
                "type=bind,src=" + str(self.project_root) + ",dst=/repo,readonly",
                "--mount",
                "type=bind,src=" + str(directory) + ",dst=/output",
                "--entrypoint",
                "env",
                image,
                "-u",
                "ATEN_CPU_CAPABILITY",
                "python",
                "/repo/env/release-era/render_artifact.py",
                "--request",
                "/output/request.json",
                "--output",
                "/output",
                "--source-root",
                "/opt/torchsynth",
            ]
            result = subprocess.run(
                command, capture_output=True, timeout=600, check=False
            )
            require(
                result.returncode == 0,
                "worker failed: " + result.stderr.decode(errors="replace")[-2000:],
            )
            observed = loads((directory / "observation.json").read_bytes())
            for name in ("audio", "noise", "pre_normalization"):
                observed[name] = (directory / (name + ".f32le")).read_bytes()
            receipt = observed["receipt"]
            require(
                receipt["request_sha256"] == digest(json_bytes(request)),
                "worker request mismatch",
            )
            require(
                receipt["worker_sha256"]
                == sha256_file(
                    self.project_root / "env/release-era/render_artifact.py"
                ),
                "worker implementation mismatch",
            )
            _, expected_runtime = qualification()
            require(
                receipt["runtime"] == expected_runtime,
                "actual worker outside qualified identity",
            )
            receipt.update(
                runtime_profile=PROFILE,
                host=host,
                image=image,
                command=command,
                exit_code=result.returncode,
                stdout_sha256=digest(result.stdout),
                stderr_sha256=digest(result.stderr),
            )
        require(
            project_identity(self.project_root) == request["project_git"],
            "producer changed during render",
        )
        return observed
