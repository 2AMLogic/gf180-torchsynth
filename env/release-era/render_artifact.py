"""Python 3.9 process boundary for the public artifact adapter (no root package).

Only the pinned Voice performs DSP. A passive profiler observes the actual
normalization argument; optional capture providers run during this same call.
"""

import argparse
import contextlib
import datetime
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys
import uuid
import warnings

from qualify_repeatability import (
    REPO,
    THREAD_ENV,
    load_plan,
    runtime_record,
    source_gate,
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def render_selected(request, source_root, *, capture_provider=None):
    """Provider(request, voice, slot) is a context manager yielding named bytes.

    It attaches observers before Voice.forward and fills its mapping before
    context exit. No provider or named-trace implementation ships in this issue.
    """
    started_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    execution_id = str(uuid.uuid4())
    if "torch" in sys.modules or "torchsynth" in sys.modules:
        raise ValueError("numerical import preceded admission")
    source_hashes = source_gate(source_root)
    plan = load_plan()
    expected_env = dict(THREAD_ENV, **plan["profile_environment"]["release"])
    if any(os.environ.get(k) != v for k, v in expected_env.items()):
        raise ValueError("worker environment outside explicit profile")
    qualification_bytes = (
        REPO / "sim/reference/repeatability-runtime.json"
    ).read_bytes()
    if (
        sha256(qualification_bytes)
        != "5904089505c05c30a456cb5e162bde89e40218ec0d0151ffb27053ec1ac4cbe4"
    ):
        raise ValueError("ratified runtime publication changed")
    expected_runtime = next(
        r["worker"]["runtime"]
        for r in json.loads(qualification_bytes)["records"]
        if r["directory"] == "release-32-1"
    )
    upstream_bytes = (REPO / "spec/reference/upstream.json").read_bytes()
    upstream = json.loads(upstream_bytes)
    expected_source = dict(
        commit=upstream["target_commit"],
        manifest_sha256=sha256(upstream_bytes),
        files=upstream["files"],
    )
    expected_profile = dict(
        name=upstream["profile"],
        contract_sha256=sha256((REPO / "spec/VOICE-CONTRACT.md").read_bytes()),
        sample_rate=44100,
        control_rate=441,
        duration_seconds=4,
        expected_sample_count=176400,
        channels=1,
        dtype="float32",
        nebula="default",
        normalization="conditional-whole-clip-peak",
    )
    runtime_descriptor = dict(
        lock_sha256=expected_runtime["lock_sha256"],
        os=expected_runtime["platform"],
        architecture=expected_runtime["machine"],
        cpu="sha256-" + sha256(expected_runtime["cpu"].encode()),
        device="cpu",
        versions={
            **{
                k: expected_runtime[k]
                for k in ("python", "torch", "lightning", "numpy")
            },
            "torchsynth": "source-" + upstream["target_commit"],
        },
    )
    if (
        request["source"] != expected_source
        or request["profile"] != expected_profile
        or request["runtime"] != runtime_descriptor
        or sha256((REPO / "env/release-era/requirements.lock").read_bytes())
        != expected_runtime["lock_sha256"]
        or sha256((REPO / "env/release-era/Dockerfile").read_bytes())
        != plan["runtime_definitions"]["release"]["dockerfile_sha256"]
    ):
        raise ValueError("request source/runtime/configuration pin mismatch")
    inventory = {
        p["name"]: p
        for p in json.loads(
            (REPO / "spec/reference/parameter-inventory-v1.json").read_text()
        )["parameters"]
    }
    for name, value in request["locks_physical"].items():
        if (
            name not in inventory
            or type(value) not in (int, float)
            or not math.isfinite(value)
            or not inventory[name]["minimum"] <= value <= inventory[name]["maximum"]
        ):
            raise ValueError("invalid named physical lock")
    packages = dict(
        sorted(
            (d.metadata["Name"].lower().replace("_", "-"), d.version)
            for d in importlib.metadata.distributions()
        )
    )
    if packages != expected_runtime["packages"]:
        raise ValueError("installed package identity differs from measured worker")
    import platform

    if (
        platform.python_version(),
        platform.platform(),
        platform.machine(),
        Path("/proc/cpuinfo").read_text(),
    ) != (
        expected_runtime["python"],
        expected_runtime["platform"],
        expected_runtime["machine"],
        expected_runtime["cpu"],
    ):
        raise ValueError("worker host identity differs from measured scope")
    size = request["execution"]["batch_size"]
    index = request["fixture"]["sound_index"]
    if (
        request["execution"]
        != dict(mode="canonical-batch", batch_size=size, reproducible=True)
        or size != 32
    ):
        raise ValueError("unqualified execution")
    if request["requested_traces"] and capture_provider is None:
        raise ValueError("requested capture provider unavailable")
    sys.path.insert(0, str(source_root))
    import torch
    from torchsynth.config import SynthConfig, check_for_reproducibility
    from torchsynth.synth import Voice
    from torchsynth.util import normalize_if_clipping

    if (
        Path(sys.modules["torchsynth.synth"].__file__).resolve()
        != (source_root / "torchsynth/synth.py").resolve()
    ):
        raise ValueError("import did not use source-gated files")
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    runtime = runtime_record("release", plan, torch, packages)
    if runtime != expected_runtime:
        raise ValueError("worker build or thread identity differs from qualification")
    check_for_reproducibility()
    slot = index % size
    voice = (
        Voice(SynthConfig(batch_size=size, **plan["configuration"]), nebula="default")
        .cpu()
        .eval()
    )
    expected_names = {
        ".".join(p["name"][:2])
        for p in json.loads(
            (source_root / "torchsynth/nebulae/voice/default.json").read_text()
        )
    }
    locks = request["locks_physical"]
    if not set(locks) <= expected_names:
        raise ValueError("unknown named lock")
    for name, value in locks.items():
        voice.set_parameters(
            {
                tuple(name.split(".", 1)): torch.full(
                    (size,), value, dtype=torch.float32
                )
            },
            freeze=True,
        )
    captured = {}

    def observe_noise(module, args, result):
        captured["noise"] = result[slot].detach().clone()

    def profile(frame, event, arg):
        if frame.f_code is normalize_if_clipping.__code__ and event == "call":
            if "pre_normalization" in captured:
                raise ValueError("multiple normalization calls")
            captured["pre_normalization"] = (
                frame.f_locals["signal"][slot].detach().clone()
            )

    if sys.getprofile() is not None:
        raise ValueError("existing profiler would be displaced")
    handle = voice.noise.register_forward_hook(observe_noise)
    provider = (
        capture_provider(request, voice, slot)
        if capture_provider
        else contextlib.nullcontext({})
    )
    try:
        with provider as traces, torch.inference_mode():
            sys.setprofile(profile)
            try:
                audio, forward, labels = voice(index // size)
            finally:
                sys.setprofile(None)
    finally:
        handle.remove()
    captured["audio"] = audio[slot].detach().clone()
    if (
        audio.shape != (size, 176400)
        or forward.shape != (size, 78)
        or labels.shape != (size,)
    ):
        raise ValueError("forward shape mismatch")
    if (
        audio.dtype != torch.float32
        or forward.dtype != torch.float32
        or labels.dtype != torch.bool
    ):
        raise ValueError("forward dtype mismatch")
    if bool(labels[slot]) != request["fixture"]["is_train"]:
        raise ValueError("train/test mismatch")
    if not torch.equal(captured["noise"], voice.noise.noise[index % 32]):
        raise ValueError("wrong selected noise stream")
    parameters = voice.get_parameters(include_frozen=True)
    normalized = {".".join(k): float(p.detach()[slot]) for k, p in parameters.items()}
    physical = {
        ".".join(k): float(p.from_0to1().detach()[slot]) for k, p in parameters.items()
    }
    by_id = {id(p): ".".join(k) for k, p in parameters.items()}
    if set(normalized) != expected_names or len(normalized) != 78:
        raise ValueError("parameter inventory mismatch")
    if forward[slot].tolist() != [normalized[by_id[id(p)]] for p in voice.parameters()]:
        raise ValueError("positional parameters disagree with named parameters")
    payloads = {}
    for name in ("audio", "noise", "pre_normalization"):
        value = captured[name]
        if (
            value.shape != (176400,)
            or value.dtype != torch.float32
            or not bool(torch.isfinite(value).all())
        ):
            raise ValueError("invalid observed payload: " + name)
        payloads[name] = (
            value.cpu().contiguous().numpy().astype("<f4", copy=False).tobytes()
        )
    if set(traces) != set(request["requested_traces"]):
        raise ValueError("capture names mismatch")
    observation = dict(
        parameters=dict(
            normalized_by_name=normalized,
            physical_by_name=physical,
            locks_physical=locks,
        ),
        noise_slot=index % 32,
        noise_sha256=sha256(payloads["noise"]),
        traces=traces,
        receipt=dict(
            schema="torchsynth-artifact-worker",
            schema_version=1,
            execution_id=execution_id,
            process_id=os.getpid(),
            started_utc=started_utc,
            source_sha256=source_hashes,
            worker_sha256=sha256(Path(__file__).read_bytes()),
            runtime=runtime,
            source_validated_before_import=True,
            rng_sentinel="PASS",
        ),
    )
    return observation, payloads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    request_bytes = args.request.read_bytes()
    with warnings.catch_warnings(record=True) as seen:
        observation, payloads = render_selected(
            json.loads(request_bytes), args.source_root
        )
    observation["receipt"]["warning_categories"] = sorted(
        {w.category.__name__ for w in seen}
    )
    observation["receipt"]["request_sha256"] = sha256(request_bytes)
    for name, data in payloads.items():
        (args.output / (name + ".f32le")).write_bytes(data)
        observation[name] = name + ".f32le"
    (args.output / "observation.json").write_text(
        json.dumps(observation, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
