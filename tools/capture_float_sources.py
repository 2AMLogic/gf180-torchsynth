"""Capture pinned-upstream source traces for the #41 float source models.

Renders the five preregistered directed Voice cases selected for oscillator
frequency/phase/shape/clamp coverage through the qualified release-era
environment (the image identity recorded in
``sim/reference/release-era-environment.json``), attaching the landed #23
passive trace capture around the single Voice call per case, exactly like the
landed ``tools/qualify_trace_artifacts.py`` traced provider. From the same
renders it records the 32 canonical noise slot digests straight from the
pinned torch generator, per-case exp2 agreement on the reachable pitch-path
inputs, and the torch-side frequency-increment digest.

Outputs (host side):

- ``tests/fixtures/float-sources/<case>/...`` one directory per case with the
  four declared checkpoint buffers (upsampled pitch controls and raw VCO
  outputs) plus ``params.json``;
- ``tests/fixtures/float-sources/noise-streams.json``;
- ``sim/reference/float-sources-v1.json`` bounded evidence record, including
  the host-side float-model comparison measurements and declared limits.

This tool executes renders; it qualifies no runtime and ratifies no numeric
contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_sources as fs  # noqa: E402
from torchsynth_voice import paired_metrics as pm  # noqa: E402

RELEASE_RECORD = ROOT / "sim/reference/release-era-environment.json"
MANIFEST_PATH = ROOT / "spec/reference/directed-voice-v1.json"
FIXTURES = ROOT / "tests/fixtures/float-sources"
RECORD_PATH = ROOT / "sim/reference/float-sources-v1.json"

CASES = [
    "boundary:vco_1.initial_phase:upper",
    "waveform:vco_2:saw",
    "boundary:vco_1.mod_depth:upper",
    "boundary:vco_2.mod_depth:upper",
    "boundary:vco_1.tuning:upper",
]
TRACES = [
    "control_upsample.vco_1_pitch",
    "vco_1.raw",
    "control_upsample.vco_2_pitch",
    "vco_2.raw",
]
SOUND_INDEX = 0
LIMITS = {
    "boundary:vco_1.initial_phase:upper": 1e-4,
    "waveform:vco_2:saw": 1e-3,
    # Swing cases accumulate binary32-cumsum phase drift from the few
    # reachable pitch inputs where binary64-libm exp2 rounds differently
    # from torch's SLEEF binary32 exp2 (7 of 7675 measured here). A single
    # mismatching input sitting on the ADSR sustain plateau shifts every
    # visiting sample's increment by one binary32 ulp, which accumulates
    # ~3.5e-3 rad over the plateau (~33k samples). The limit accommodates
    # several such plateaus plus cross-platform libm rounding variance.
    "boundary:vco_1.mod_depth:upper": 5e-2,
    "boundary:vco_2.mod_depth:upper": 5e-2,
    "boundary:vco_1.tuning:upper": 5e-5,
}
COMPARISON_UNIT = "linear-amplitude"

# Python 3.9 driver executed inside the qualified image; no root-package
# imports (the image cannot import the >=3.11 package). One forked child per
# case because the landed worker refuses a process where Torch was already
# imported. Writes everything under /output.
DRIVER_SOURCE = '''\
"""Container-side #41 source-trace capture; Python 3.9, offline."""
import hashlib
import json
import os
import struct
import sys
import warnings
from decimal import Decimal, getcontext
from pathlib import Path

for name in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[name] = "1"
os.environ["MKL_CBWR"] = "COMPATIBLE"
os.environ.pop("ATEN_CPU_CAPABILITY", None)

sys.path.insert(0, "/repo/env/release-era")
sys.path.insert(0, "/repo/src/torchsynth_voice")

import render_artifact as worker
import trace_capture_provider
import trace_registry

CASES = json.loads(Path("/output/request-cases.json").read_text())
TRACES = json.loads(Path("/output/request-traces.json").read_text())
SOUND_INDEX = json.loads(Path("/output/request-sound-index.json").read_text())

_f32 = struct.Struct("<f")
_u32 = struct.Struct("<I")


def f32(x):
    return _f32.unpack(_f32.pack(x))[0]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def runtime_descriptor():
    qualification = json.loads(
        (Path("/repo") / "sim/reference/repeatability-runtime.json").read_text()
    )
    expected_runtime = next(
        r["worker"]["runtime"]
        for r in qualification["records"]
        if r["directory"] == "release-32-1"
    )
    upstream = json.loads(
        (Path("/repo") / "spec/reference/upstream.json").read_text()
    )
    return dict(
        lock_sha256=expected_runtime["lock_sha256"],
        os=expected_runtime["platform"],
        architecture=expected_runtime["machine"],
        cpu="sha256-" + sha256_bytes(expected_runtime["cpu"].encode()),
        device="cpu",
        versions={
            **{
                k: expected_runtime[k]
                for k in ("python", "torch", "lightning", "numpy")
            },
            "torchsynth": "source-" + upstream["target_commit"],
        },
    )


def base_request():
    upstream_bytes = (
        Path("/repo") / "spec/reference/upstream.json"
    ).read_bytes()
    upstream = json.loads(upstream_bytes)
    document = trace_registry.load_registry()
    requested = trace_registry.requested_names(document, TRACES)
    if requested != TRACES:
        raise ValueError("requested traces drifted from registry order")
    return dict(
        source=dict(
            commit=upstream["target_commit"],
            manifest_sha256=sha256_bytes(upstream_bytes),
            files=upstream["files"],
        ),
        profile=dict(
            name=upstream["profile"],
            contract_sha256=sha256_bytes(
                (Path("/repo") / "spec/VOICE-CONTRACT.md").read_bytes()
            ),
            sample_rate=44100,
            control_rate=441,
            duration_seconds=4,
            expected_sample_count=176400,
            channels=1,
            dtype="float32",
            nebula="default",
            normalization="conditional-whole-clip-peak",
        ),
        runtime=runtime_descriptor(),
        execution=dict(mode="canonical-batch", batch_size=32, reproducible=True),
        fixture=dict(sound_index=SOUND_INDEX, is_train=True),
        locks_physical={},
        requested_traces=requested,
        trace_registry_version=trace_registry.registry_token(),
    )


def resolve_patch(manifest, case_id):
    case = next(c for c in manifest["cases"] if c["id"] == case_id)
    patch = {name: row["physical"] for name, row in manifest["base"].items()}
    for name, row in case["overrides"].items():
        patch[name] = row["physical"]
    return patch, case


def render_case(case_id, request):
    manifest = json.loads(
        (Path("/repo") / "spec/reference/directed-voice-v1.json").read_text()
    )
    patch, case = resolve_patch(manifest, case_id)
    document = trace_registry.load_registry()
    request = dict(request)
    request["locks_physical"] = patch
    factory = trace_capture_provider.ProviderFactory(
        document, request["requested_traces"], request["execution"]["batch_size"]
    )
    with warnings.catch_warnings(record=True) as seen:
        observation, payloads = worker.render_selected(
            request, Path("/opt/torchsynth"), capture_provider=factory
        )
    receipt = observation["receipt"]
    receipt["warning_categories"] = sorted({w.category.__name__ for w in seen})
    out = Path("/output") / case_id
    out.mkdir(parents=True)
    files = {}
    for name in TRACES:
        data = factory.payloads[name]
        (out / (name + ".f32le")).write_bytes(data)
        files[name] = dict(sha256=sha256_bytes(data), size=len(data))
    params = dict(
        case_id=case_id,
        target=case["target"],
        variant=case.get("variant", ""),
        purpose=case.get("purpose", ""),
        directed_identity=manifest["identity"],
        sound_index=SOUND_INDEX,
        noise_slot=SOUND_INDEX % 32,
        is_train=True,
        requested_traces=list(request["requested_traces"]),
        normalized_by_name=observation["parameters"]["normalized_by_name"],
        physical_by_name=observation["parameters"]["physical_by_name"],
        noise_sha256=observation["noise_sha256"],
        receipt=dict(
            execution_id=receipt["execution_id"],
            started_utc=receipt["started_utc"],
            runtime=receipt["runtime"],
            worker_sha256=receipt["worker_sha256"],
        ),
    )
    params_bytes = json.dumps(
        params, sort_keys=True, indent=2, allow_nan=False
    ).encode() + b"\\n"
    (out / "params.json").write_bytes(params_bytes)
    return dict(
        case_id=case_id,
        files=files,
        params_sha256=hashlib.sha256(params_bytes).hexdigest(),
        noise_sha256=observation["noise_sha256"],
    )


def exact_exp2_f32(value):
    """Correctly rounded binary32 of 2**value via 60-digit decimal exp."""
    getcontext().prec = 60
    exact = (Decimal(value) * Decimal(2).ln()).exp()
    return f32(float(exact))


def reachable_exponents(case_id, vco, control_name):
    """Distinct f32 (clamp(midi+depth*control) - 69)/12 inputs for one case."""
    params = json.loads(
        (Path("/output") / case_id / "params.json").read_text()
    )
    physical = params["physical_by_name"]
    control = struct.unpack(
        "<176400f",
        (Path("/output") / case_id / (control_name + ".f32le")).read_bytes(),
    )
    midi = f32(physical["keyboard.midi_f0"] + physical[vco + ".tuning"])
    depth = f32(physical[vco + ".mod_depth"])
    seen = set()
    for sample in control:
        value = f32(midi + f32(depth * f32(sample)))
        if value < 0.0:
            value = 0.0
        elif value > 127.0:
            value = 127.0
        seen.add(f32((value - 69.0) / 12.0))
    return sorted(seen)


def exp2_agreement(case_id):
    """Torch exp2 vs correctly-rounded exp2 on the reachable pitch inputs."""
    import torch

    out = {}
    for vco, control_name in (
        ("vco_1", "control_upsample.vco_1_pitch"),
        ("vco_2", "control_upsample.vco_2_pitch"),
    ):
        values = reachable_exponents(case_id, vco, control_name)
        tensor = torch.tensor(values, dtype=torch.float32)
        observed = torch.exp2(tensor).tolist()
        mismatches = 0
        max_ulp = 0
        for value, got in zip(values, observed):
            expected = exact_exp2_f32(value)
            if _f32.pack(got) != _f32.pack(expected):
                mismatches += 1
                distance = abs(
                    _u32.unpack(_f32.pack(got))[0]
                    - _u32.unpack(_f32.pack(expected))[0]
                )
                max_ulp = max(max_ulp, distance)
        out[vco] = dict(
            reachable_inputs=len(values),
            exp2_mismatches=mismatches,
            max_ulp=max_ulp,
        )
    return out


def torch_increment_digests(case_id):
    """Digest of the torch-side per-sample frequency increments, per VCO."""
    import torch

    torch.pi = torch.acos(torch.zeros(1)).item() * 2
    params = json.loads(
        (Path("/output") / case_id / "params.json").read_text()
    )
    physical = params["physical_by_name"]
    out = {}
    for vco, control_name in (
        ("vco_1", "control_upsample.vco_1_pitch"),
        ("vco_2", "control_upsample.vco_2_pitch"),
    ):
        control = torch.frombuffer(
            bytearray(
                (Path("/output") / case_id / (control_name + ".f32le")).read_bytes()
            ),
            dtype=torch.float32,
        )
        midi = torch.tensor(
            [physical["keyboard.midi_f0"]], dtype=torch.float32
        ) + torch.tensor([physical[vco + ".tuning"]], dtype=torch.float32)
        depth = torch.tensor([physical[vco + ".mod_depth"]], dtype=torch.float32)
        control_as_frequency = torch.clamp(midi + depth * control, 0.0, 127.0)
        increments = (
            2
            * torch.pi
            * (440.0 * torch.exp2((control_as_frequency - 69.0) / 12.0))
            / 44100.0
        )
        data = increments.contiguous().numpy().astype("<f4", copy=False).tobytes()
        out[vco] = dict(sha256=hashlib.sha256(data).hexdigest())
    return out


def noise_streams():
    import torch

    generator = torch.Generator(device="cpu").manual_seed(13)
    noise = torch.empty((32, 176400), device="cpu")
    noise.data.uniform_(-1.0, 1.0, generator=generator)
    slots = {}
    for index in range(32):
        data = noise[index].contiguous().numpy().astype("<f4", copy=False).tobytes()
        slots[str(index)] = dict(
            sha256=hashlib.sha256(data).hexdigest(),
            first_hex=data[:16].hex(),
            last_hex=data[-16:].hex(),
        )
    return dict(
        seed=13,
        streams=32,
        sample_count=176400,
        encoding="f32le",
        slots=slots,
    )


def write_json(path, payload):
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\\n"
    )


def main():
    output = Path("/output")
    request = base_request()
    summaries = {}
    for case_id in CASES:
        pid = os.fork()
        if pid == 0:
            try:
                summary = render_case(case_id, request)
                write_json(output / ("case-" + case_id + ".json"), summary)
            except BaseException as error:  # noqa: BLE001
                (output / ("case-" + case_id + ".error")).write_text(repr(error))
                os._exit(1)
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        if status != 0:
            raise RuntimeError("case render failed: " + case_id)
        summaries[case_id] = json.loads(
            (output / ("case-" + case_id + ".json")).read_text()
        )
    streams = noise_streams()
    write_json(output / "noise-streams.json", streams)
    agreement = {}
    digests = {}
    for case_id in CASES:
        agreement[case_id] = exp2_agreement(case_id)
        digests[case_id] = torch_increment_digests(case_id)
    write_json(
        output / "capture-manifest.json",
        dict(
            cases=CASES,
            case_summaries=summaries,
            exp2_agreement=agreement,
            torch_increment_digests=digests,
            noise_slot0_sha256=streams["slots"]["0"]["sha256"],
        ),
    )


if __name__ == "__main__":
    main()
'''


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_bytes(record):
    return (
        json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def qualified_image():
    record = json.loads(RELEASE_RECORD.read_bytes())
    image = record["image"]["id"]
    inspection = json.loads(
        subprocess.check_output(["docker", "image", "inspect", image])
    )[0]
    if inspection["Os"] != "linux" or inspection["Architecture"] != "amd64":
        raise RuntimeError("qualified image mismatch: " + image)
    return image, record


def run_capture(staging):
    image, release_record = qualified_image()
    (staging / "request-cases.json").write_text(json.dumps(CASES))
    (staging / "request-traces.json").write_text(json.dumps(TRACES))
    (staging / "request-sound-index.json").write_text(json.dumps(SOUND_INDEX))
    (staging / "driver.py").write_text(DRIVER_SOURCE)
    command = [
        "docker", "run", "--rm", "--pull", "never",
        "--platform", "linux/amd64", "--network", "none",
        "--memory", "6g", "--cpus", "1",
        "--env", "OMP_NUM_THREADS=1",
        "--env", "MKL_NUM_THREADS=1",
        "--env", "MKL_CBWR=COMPATIBLE",
        "--mount", "type=bind,src=" + str(ROOT) + ",dst=/repo,readonly",
        "--mount", "type=bind,src=" + str(staging) + ",dst=/output",
        "--entrypoint", "env",
        image,
        "-u", "ATEN_CPU_CAPABILITY",
        "python3", "/output/driver.py",
    ]
    result = subprocess.run(command, capture_output=True, timeout=5400, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "capture driver failed:\n"
            + result.stderr.decode(errors="replace")[-4000:]
        )
    manifest = json.loads((staging / "capture-manifest.json").read_bytes())
    return manifest, command, image, release_record


def model_case_measurement(case_dir):
    """Host-side float-model render for one case; per-VCO paired metrics."""
    params = json.loads((case_dir / "params.json").read_bytes())
    physical = params["physical_by_name"]
    midi_f0 = physical["keyboard.midi_f0"]
    result = {}
    for vco, model in (
        ("vco_1", fs.SineVCO),
        ("vco_2", fs.SquareSawVCO),
    ):
        kwargs = dict(
            tuning=physical[vco + ".tuning"],
            mod_depth=physical[vco + ".mod_depth"],
            initial_phase=physical[vco + ".initial_phase"],
        )
        if model is fs.SquareSawVCO:
            kwargs["shape"] = physical[vco + ".shape"]
        instance = model(**kwargs)
        control = fs.f32le_values(
            (case_dir / ("control_upsample." + vco + "_pitch.f32le")).read_bytes()
        )
        rendered = instance.output(midi_f0, control)
        reference = fs.f32le_values(
            (case_dir / (vco + ".raw.f32le")).read_bytes()
        )
        measurement = pm.compare_paired(
            reference,
            rendered,
            reference_rate_hz=fs.AUDIO_RATE_HZ,
            candidate_rate_hz=fs.AUDIO_RATE_HZ,
            unit=COMPARISON_UNIT,
            spectral=False,
        )
        metrics = measurement["metrics"]
        result[vco] = dict(
            framing_match=metrics["framing_match"]["value"],
            exact_equal=metrics["exact_equal"]["value"],
            max_abs_error=metrics["max_abs_error"]["value"],
            mean_abs_error=metrics["mean_abs_error"]["value"],
            error_rms=metrics["error_rms"]["value"],
            rendered_sha256=hashlib.sha256(
                fs.f32le_bytes(rendered)
            ).hexdigest(),
            limit=LIMITS[params["case_id"]],
        )
    return result


def assemble(staging, manifest, command, image, release_record):
    fixtures = FIXTURES
    fixtures.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        staging / "noise-streams.json", fixtures / "noise-streams.json"
    )
    fixture_digests = {}
    for case_id in manifest["cases"]:
        destination = fixtures / case_id
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(staging / case_id, destination)
        summary = manifest["case_summaries"][case_id]
        fixture_digests[case_id] = dict(
            files=summary["files"],
            params_sha256=summary["params_sha256"],
            noise_sha256=summary["noise_sha256"],
        )

    noise_document = json.loads((fixtures / "noise-streams.json").read_bytes())
    model_bytes = fs.canonical_noise_slot_bytes(
        13, SOUND_INDEX, fs.NOISE_STREAMS, fs.AUDIO_SAMPLES
    )
    model_digest = hashlib.sha256(model_bytes).hexdigest()
    slot0_digest = noise_document["slots"]["0"]["sha256"]

    measurements = {}
    for case_id in manifest["cases"]:
        measurements[case_id] = model_case_measurement(fixtures / case_id)

    record = dict(
        schema="torchsynth-float-sources-evidence",
        schema_version=1,
        semantic_version="float-sources-v1",
        purpose=(
            "Issue #41 evidence: independent float VCO and noise models "
            "matched against the pinned Voice at the declared source "
            "checkpoints; noise bit-exact by identity, oscillators under "
            "declared metrics"
        ),
        recorded_from_tool=sha256_file(Path(__file__)),
        model_module_sha256=sha256_file(
            ROOT / "src/torchsynth_voice/float_sources.py"
        ),
        calculation_policy=fs.CALCULATION_POLICY.describe(),
        numeric_contract="unbound:#53",
        source_commit=fs.SOURCE_COMMIT,
        directed_manifest=json.loads(MANIFEST_PATH.read_bytes())["identity"],
        sound_index=SOUND_INDEX,
        comparison_traces=TRACES,
        comparison_unit=COMPARISON_UNIT,
        limits=LIMITS,
        execution=dict(
            command=command,
            image=image,
            release_record_sha256=sha256_file(RELEASE_RECORD),
            docker_server=release_record.get("docker_server", "unknown"),
        ),
        capture=manifest,
        fixtures=fixture_digests,
        noise=dict(
            seed=13,
            slot=SOUND_INDEX % fs.NOISE_STREAMS,
            sample_count=fs.AUDIO_SAMPLES,
            declared_slot0_sha256=slot0_digest,
            model_regeneration_sha256=model_digest,
            bit_exact=model_digest == slot0_digest,
            all_slots_sha256=sha256_file(fixtures / "noise-streams.json"),
        ),
        measurements=measurements,
        limitations=[
            "VCO comparisons are declared-metrics (DR-0007): binary32 "
            "transcendental sites (exp2/sin/cos/tanh/log10) use binary64 "
            "libm rounded to binary32 while upstream uses SLEEF binary32 "
            "kernels, so near-one-ulp sample differences are expected and "
            "no VCO byte identity is claimed.",
            "Swing-case limits are dominated by a measured phase-drift "
            "mechanism: a reachable pitch input where libm exp2 rounds "
            "differently from torch SLEEF exp2 (7 of 7675 inputs, one ulp "
            "each) shifts the increment of every sample visiting it; on the "
            "ADSR sustain plateau this accumulates to ~3.5e-3 rad. The 5e-2 "
            "limits cover several such plateaus plus cross-platform libm "
            "variance; constant-frequency cases see no accumulation and "
            "stay near one binary32 ulp of the waveform.",
            "The phase cumsum reproduces the pinned CPU binary64-accumulator "
            "algorithm (f32 stored partials, no wrap); the fixed model's "
            "wrapping accumulator (DR-0008, Proposed) is deliberately not "
            "implemented here.",
            "Noise exactness covers the resolved slot streams; larger "
            "reproducible batches repeat the 32 streams (pinned config "
            "behavior, not re-measured here).",
            "No runtime, fixed-model, hardware, or sound-fidelity claim is "
            "established by this record.",
        ],
    )
    RECORD_PATH.write_bytes(json_bytes(record))
    return record


def check():
    record = json.loads(RECORD_PATH.read_bytes())
    problems = []
    for case_id, entries in record["fixtures"].items():
        directory = FIXTURES / case_id
        if not directory.is_dir():
            problems.append("missing case fixtures: " + case_id)
            continue
        if sha256_file(directory / "params.json") != entries["params_sha256"]:
            problems.append("stale params fixture: " + case_id)
        for name, entry in entries["files"].items():
            path = directory / (name + ".f32le")
            if not path.exists():
                problems.append("missing trace fixture: %s/%s" % (case_id, name))
            elif sha256_file(path) != entry["sha256"]:
                problems.append("stale trace fixture: %s/%s" % (case_id, name))
    noise_path = FIXTURES / "noise-streams.json"
    if not noise_path.exists():
        problems.append("missing noise-streams.json")
    elif sha256_file(noise_path) != record["noise"]["all_slots_sha256"]:
        problems.append("stale noise-streams.json")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify committed fixtures/record"
    )
    parser.add_argument("--staging", type=Path, default=None)
    parser.add_argument(
        "--assemble-only",
        action="store_true",
        help="rebuild fixtures/record from an existing staging directory",
    )
    args = parser.parse_args()
    if args.check:
        problems = check()
        if problems:
            print("\n".join(problems))
            raise SystemExit(1)
        print("float-sources fixtures and record are consistent")
        return
    staging = Path(
        args.staging or tempfile.mkdtemp(prefix="float-sources-capture-")
    ).resolve()
    staging.mkdir(parents=True, exist_ok=True)
    if args.assemble_only:
        manifest = json.loads((staging / "capture-manifest.json").read_bytes())
        record = assemble(staging, manifest, [], "assemble-only", {})
    else:
        manifest, command, image, release_record = run_capture(staging)
        record = assemble(staging, manifest, command, image, release_record)
    print("cases: " + ", ".join(record["capture"]["cases"]))
    print("noise slot 0 bit-exact: %s" % record["noise"]["bit_exact"])
    for case_id, per_vco in record["measurements"].items():
        for vco, entry in per_vco.items():
            print(
                "%s %s max_abs_error=%.3g limit=%.1e"
                % (case_id, vco, entry["max_abs_error"], entry["limit"])
            )
    print("record: " + str(RECORD_PATH))


if __name__ == "__main__":
    main()
