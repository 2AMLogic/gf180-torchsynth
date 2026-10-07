"""The v1 artifact adapter. DSP runs in a separately admitted Python 3.9 process.

The public request/observation seam permits same-render capture providers; see
spec/CORPUS-RUNNER.md. Importing this module needs only the standard library.
"""

from __future__ import annotations

import copy
import json
import math
import platform
import re
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
from .digest import sha256_bytes as digest
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


# Portable attempt receipts (spec/CORPUS-RUNNER.md "Portable attempt receipts").
PORTABLE_COMMAND_REPRESENTATION = "portable-placeholders-v1"
PROJECT_ROOT_PLACEHOLDER = "<project-root>"
WORKER_OUTPUT_PLACEHOLDER = "<worker-output>"
MOUNT_PLACEHOLDERS = (PROJECT_ROOT_PLACEHOLDER, WORKER_OUTPUT_PLACEHOLDER)
#: Declared container roots, scoped by receipt key path (list indices ignored).
CONTAINER_PATH_ROOTS = {
    ("command",): ("/repo", "/output", "/opt/torchsynth"),
    ("runtime", "torch_build"): ("/opt/rh/devtoolset-9",),
}
FAILURE_RECEIPT_FIELDS = frozenset(
    {"error_type", "message", "message_sha256", "message_status"}
)
_TOKEN_START = r"(?:^|(?<=[\s=,:;\"'()\[\]{}<>|]))"
_TOKEN_BODY = r"[^\s,;\"'()\[\]{}<>|]*"
_POSIX_PATH = re.compile(_TOKEN_START + "/" + _TOKEN_BODY)
_FOREIGN_PATHS = (
    ("home-relative", re.compile(_TOKEN_START + r"~[/\\]" + _TOKEN_BODY)),
    ("windows-drive", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]" + _TOKEN_BODY)),
    ("windows-rooted-or-unc", re.compile(_TOKEN_START + r"\\" + _TOKEN_BODY)),
)
_MOUNT_SOURCE = re.compile(r"(?:^|,)(?:src|source)=([^,]*)")


class ReceiptPortabilityError(ValidationError):
    """A receipt would publish a host path; never written to a finish record."""


def _pointer(path):
    """Location text that can never itself contain a path token or raw key."""
    text = "receipt"
    for part in path:
        if type(part) is int:
            text += f"[{part}]"
        else:
            safe = re.fullmatch(r"[A-Za-z0-9_<>-]+", part)
            text += "." + (part if safe else "<key>")
    return text


def _string_findings(text, path):
    keys = tuple(p for p in path if type(p) is str)
    roots = CONTAINER_PATH_ROOTS.get(keys, ())
    findings = []
    for match in _POSIX_PATH.finditer(text):
        token = match.group(0)
        allowed = ".." not in token.split("/") and any(
            token == root or token.startswith(root + "/") for root in roots
        )
        if not allowed:
            findings.append((_pointer(path), "posix-absolute"))
    for kind, pattern in _FOREIGN_PATHS:
        if pattern.search(text):
            findings.append((_pointer(path), kind))
    return findings


def host_path_findings(value, path=()):
    """Return (location, kind) for every host-path token in a receipt.

    Keys and string values are scanned at every depth; only the declared
    field-scoped container roots are exempt. Locations never echo the
    offending text or any key outside ``[A-Za-z0-9_<>-]``.
    """
    findings = []
    if type(value) is dict:
        for key, child in value.items():
            findings.extend(_string_findings(str(key), path + ("<key>",)))
            findings.extend(host_path_findings(child, path + (key,)))
    elif type(value) in (list, tuple):
        for index, child in enumerate(value):
            findings.extend(host_path_findings(child, path + (index,)))
    elif type(value) is str:
        findings.extend(_string_findings(value, path))
    return findings


def require_portable_receipt(receipt):
    """Fail closed unless a receipt satisfies the declared portable representation."""
    if type(receipt) is not dict:
        raise ReceiptPortabilityError("attempt receipt must be an object")
    findings = host_path_findings(receipt)
    if findings:
        raise ReceiptPortabilityError(
            "attempt receipt carries host paths at "
            + ", ".join(f"{p} ({k})" for p, k in findings)
        )
    if "command" not in receipt:
        return
    command = receipt["command"]
    if receipt.get("command_representation") != PORTABLE_COMMAND_REPRESENTATION:
        raise ReceiptPortabilityError(
            "receipt command lacks the portable command representation marker"
        )
    if type(command) is not list or not all(type(a) is str for a in command):
        raise ReceiptPortabilityError("receipt command must be a list of strings")
    for index, argument in enumerate(command):
        sources = [m.group(1) for m in _MOUNT_SOURCE.finditer(argument)]
        if argument.startswith(("--volume=", "-v=")):
            sources.append(argument.split("=", 1)[1].split(":", 1)[0])
        elif index and command[index - 1] in ("-v", "--volume"):
            sources.append(argument.split(":", 1)[0])
        elif len(argument) > 2 and argument.startswith("-v") and argument[2] != "-":
            sources.append(argument[2:].split(":", 1)[0])
        if any(source not in MOUNT_PLACEHOLDERS for source in sources):
            raise ReceiptPortabilityError(
                f"receipt command mount source at receipt.command[{index}] is not a "
                "declared placeholder"
            )


def failure_receipt(error):
    """Portable diagnostic for a failed attempt: never raw path-bearing text."""
    message = str(error)
    portable = not host_path_findings(message)
    return dict(
        error_type=re.sub(r"[^A-Za-z0-9._-]", "-", type(error).__name__),
        message=message if portable else None,
        message_sha256=digest(message.encode("utf-8", "surrogatepass")),
        message_status="portable" if portable else "withheld-host-path",
    )


def validate_failure_receipt(receipt):
    require(
        type(receipt) is dict and set(receipt) == FAILURE_RECEIPT_FIELDS,
        "failed attempt receipt is not the portable diagnostic form",
    )
    require(
        type(receipt["error_type"]) is str
        and re.fullmatch(r"[A-Za-z0-9._-]+", receipt["error_type"])
        and type(receipt["message_sha256"]) is str
        and re.fullmatch(r"[a-f0-9]{64}", receipt["message_sha256"]),
        "failed attempt diagnostic fields malformed",
    )
    status, message = receipt["message_status"], receipt["message"]
    require(
        (status == "portable" and type(message) is str)
        or (status == "withheld-host-path" and message is None),
        "failed attempt diagnostic message/status contradiction",
    )
    if message is not None:
        require(
            digest(message.encode("utf-8", "surrogatepass"))
            == receipt["message_sha256"],
            "failed attempt diagnostic digest mismatch",
        )


def release_profile_environment():
    """The declared dispatch environment of the canonical release profile.

    Read from the preregistered plan rather than restated here, and shared by
    every host-side spawn of a release-profile worker (the registry lives in
    ``tools/check_reference_consolidation.py`` as ``DISPATCH_SPAWN_SITES``).
    Each of those workers asserts this same object before it renders — e.g.
    ``env/release-era/render_artifact.py`` -> "worker environment outside
    explicit profile" — so a literal copy of the pins in a spawn path is a
    second place for the declaration to drift out of agreement with the plan.

    That drift is exactly what DR-0009 amendment A2 left behind: it added
    ``ONEDNN_MAX_CPU_ISA``/``MKL_ENABLE_INSTRUCTIONS`` to the plan, which the two
    sentinel spawn paths picked up for free because they already read the plan,
    while six other spawn paths went on describing the pre-amendment
    two-variable environment and therefore refused at the worker on first use
    (issue #3).
    """
    plan = loads(
        (repository_root() / "env/release-era/repeatability-matrix.json").read_bytes()
    )
    return plan["profile_environment"]["release"]


def dispatch_flags(profile):
    """``docker run`` ``--env`` flags for every pin the profile declares set."""
    return [
        part
        for key, value in profile.items()
        if value is not None
        for part in ("--env", key + "=" + value)
    ]


def dispatch_unset_flags(profile):
    """``env -u`` arguments for every pin the profile declares explicitly unset.

    A ``null`` in the plan is a declaration ("this selector stays at the image
    default"), not an omission, so it is enforced by unsetting the variable
    inside the container rather than by hoping the image never sets it.
    """
    return [part for key, value in profile.items() if value is None for part in ("-u", key)]


def json_bytes(value):
    canonical_bytes(value)  # reject non-JSON/nonfinite values before serialization
    return (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


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
    # A path-bearing receipt is refused before anything is published.
    require_portable_receipt(observed["receipt"])
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
        profile = release_profile_environment()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            (directory / "request.json").write_bytes(json_bytes(request))

            def launch(project_source, output_source):
                # One construction serves the executed argv (real sources) and
                # the published receipt (named placeholders), so they cannot
                # drift in image, environment, options or argument order.
                return [
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
                    *dispatch_flags(profile),
                    "--mount",
                    "type=bind,src=" + project_source + ",dst=/repo,readonly",
                    "--mount",
                    "type=bind,src=" + output_source + ",dst=/output",
                    "--entrypoint",
                    "env",
                    image,
                    *dispatch_unset_flags(profile),
                    "python",
                    "/repo/env/release-era/render_artifact.py",
                    "--request",
                    "/output/request.json",
                    "--output",
                    "/output",
                    "--source-root",
                    "/opt/torchsynth",
                ]

            command = launch(str(self.project_root), str(directory))
            portable_command = launch(
                PROJECT_ROOT_PLACEHOLDER, WORKER_OUTPUT_PLACEHOLDER
            )
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
                command=portable_command,
                command_representation=PORTABLE_COMMAND_REPRESENTATION,
                exit_code=result.returncode,
                stdout_sha256=digest(result.stdout),
                stderr_sha256=digest(result.stderr),
            )
        require(
            project_identity(self.project_root) == request["project_git"],
            "producer changed during render",
        )
        return observed
