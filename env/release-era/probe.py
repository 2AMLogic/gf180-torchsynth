"""Python 3.9 CPU smoke qualification; no import or source compatibility shims."""

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import math
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import traceback
import warnings


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def validate_source(root, manifest):
    """Only stdlib is used until every manifest file has been checked."""
    observed = {}
    for group in ("files", "source_checkout_only_files"):
        for name, expected in manifest.get(group, {}).items():
            path = root / name
            if not path.is_file():
                raise ValueError("missing source: " + name)
            observed[name] = sha256(path.read_bytes())
            if observed[name] != expected:
                raise ValueError("source hash mismatch: " + name)
    return observed


def identity(index):
    batch, slot = divmod(index, 32)
    nominal_batch, nominal_slot = divmod(index, 128)
    return {
        "sound_index": index,
        "render_batch_size": 32,
        "render_batch_index": batch,
        "render_slot": slot,
        "upstream_nominal_batch_size": 128,
        "upstream_batch_index": nominal_batch,
        "upstream_slot": nominal_slot,
        "upstream_name": "synth1B1-{}-{}".format(nominal_batch, nominal_slot),
        "noise_slot": slot,
        "is_train": (index // 1024) % 10 != 9,
    }


def negative_control(source_root, manifest_path):
    """A real changed source tree must fail in a fresh process before import."""
    with tempfile.TemporaryDirectory() as directory:
        altered = Path(directory) / "source"
        shutil.copytree(source_root, altered)
        with (altered / "torchsynth/config.py").open("a") as handle:
            handle.write("\n# deliberate source-hash negative control\n")
        result = subprocess.run(
            [
                sys.executable,
                __file__,
                "--source-root",
                str(altered),
                "--manifest",
                str(manifest_path),
                "--preflight-only",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(result.stdout)
        if (
            result.returncode != 1
            or report["torchsynth_imported"]
            or report["error"]
            != "ValueError: source hash mismatch: torchsynth/config.py"
        ):
            raise RuntimeError(
                "changed-source negative control did not reject before import"
            )
        return {
            "status": "passed",
            "exit_code": result.returncode,
            "error": report["error"],
            "torchsynth_imported": False,
            "stderr": result.stderr,
        }


def run_probe(source_root, manifest, output):
    hashes = validate_source(source_root, manifest)
    if any(
        name == "torchsynth" or name.startswith("torchsynth.") for name in sys.modules
    ):
        raise RuntimeError("TorchSynth was already imported before source validation")
    sys.path.insert(0, str(source_root))
    import numpy as np
    import torch
    from torchsynth.config import SynthConfig, check_for_reproducibility
    from torchsynth.synth import Voice

    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    check_for_reproducibility()
    expected = manifest["expected"]
    nebula = json.loads(
        (source_root / "torchsynth/nebulae/voice/default.json").read_text()
    )
    expected_names = {".".join(entry["name"][:2]) for entry in nebula}
    if len(nebula) != expected["nebula_entry_count"]:
        raise RuntimeError("nebula entry count mismatch")
    sounds = []
    output.mkdir(parents=True, exist_ok=True)
    for index in (0, 39942):
        coordinates = identity(index)
        slot = coordinates["render_slot"]
        config = SynthConfig(
            batch_size=32,
            sample_rate=expected["sample_rate"],
            buffer_size_seconds=expected["duration_seconds"],
            control_rate=expected["control_rate"],
            reproducible=True,
            no_grad=True,
        )
        voice = Voice(synthconfig=config, nebula="default").cpu().eval()
        with torch.inference_mode():
            audio, forward, is_train = voice(coordinates["render_batch_index"])
        if audio.dtype != torch.float32 or forward.dtype != torch.float32:
            raise RuntimeError("expected float32 audio and parameters")
        samples = (
            audio[slot].detach().cpu().contiguous().numpy().astype("<f4", copy=False)
        )
        if (
            samples.shape != (expected["output_samples"],)
            or not np.isfinite(samples).all()
        ):
            raise RuntimeError("incorrect sample count or nonfinite audio")
        normalized, physical, names_by_id = {}, {}, {}
        for (module, parameter_name), parameter in voice.get_parameters(
            include_frozen=True
        ).items():
            name = module + "." + parameter_name
            names_by_id[id(parameter)] = name
            normalized[name] = float(parameter.detach()[slot].item())
            physical[name] = float(parameter.from_0to1().detach()[slot].item())
        if (
            set(normalized) != expected_names
            or len(normalized) != expected["latent_parameter_count"]
        ):
            raise RuntimeError("named parameter set mismatch")
        if not all(
            math.isfinite(v)
            for v in list(normalized.values()) + list(physical.values())
        ):
            raise RuntimeError("nonfinite parameter")
        order = [names_by_id[id(parameter)] for parameter in voice.parameters()]
        if forward.shape != (32, expected["latent_parameter_count"]):
            raise RuntimeError("forward parameter tensor shape mismatch")
        if [normalized[name] for name in order] != forward[slot].tolist():
            raise RuntimeError("forward parameter tensor/name mismatch")
        if bool(is_train[slot].item()) != coordinates["is_train"]:
            raise RuntimeError("train/test identity mismatch")
        audio_file = "sound-{}.f32le".format(index)
        parameter_file = "sound-{}.parameters.json".format(index)
        parameters = json_bytes(
            {"normalized_by_name": normalized, "physical_by_name": physical}
        )
        (output / audio_file).write_bytes(samples.tobytes())
        (output / parameter_file).write_bytes(parameters + b"\n")
        sounds.append(
            {
                "identity": coordinates,
                "sample_count": int(samples.size),
                "named_parameter_count": len(normalized),
                "all_values_finite": True,
                "forward_parameter_names_verified": True,
                "audio_file": audio_file,
                "audio_sha256": sha256(samples.tobytes()),
                "parameter_file": parameter_file,
                "parameter_file_sha256": sha256(parameters + b"\n"),
                "normalized_parameters_sha256": sha256(json_bytes(normalized)),
                "physical_parameters_sha256": sha256(json_bytes(physical)),
                "peak_abs": float(np.abs(samples).max()),
                "rms": float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))),
            }
        )
        del voice, audio, forward
    check = subprocess.run(
        [sys.executable, "-m", "pip", "check"], capture_output=True, text=True
    )
    if check.returncode:
        raise RuntimeError(check.stdout + check.stderr)
    return {
        "status": "passed",
        "profile": manifest["profile"],
        "source_commit": manifest["target_commit"],
        "source_sha256": hashes,
        "source_validated_before_import": True,
        "rng_sentinel": {
            "function": "torchsynth.config.check_for_reproducibility",
            "status": "passed",
        },
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "device": "cpu",
            "dtype": "float32",
            "torch": torch.__version__,
            "torch_num_threads": torch.get_num_threads(),
            "torch_num_interop_threads": torch.get_num_interop_threads(),
            "torch_build_configuration": torch.__config__.show(),
            "packages": dict(
                sorted(
                    (dist.metadata["Name"], dist.version)
                    for dist in importlib.metadata.distributions()
                )
            ),
            "pip_check": check.stdout.strip(),
        },
        "configuration": {
            "sample_rate": expected["sample_rate"],
            "control_rate": expected["control_rate"],
            "duration_seconds": expected["duration_seconds"],
            "nebula": "default",
            "batch_size": 32,
            "reproducible": True,
            "noise_seed": expected["noise_seed"],
            "postprocessing": "none",
        },
        "parameter_names": sorted(expected_names),
        "sounds": sounds,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("/opt/torchsynth"))
    parser.add_argument(
        "--manifest", type=Path, default=Path(__file__).with_name("upstream.json")
    )
    parser.add_argument("--output", type=Path, default=Path("/output"))
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    report = {"status": "failed"}
    stdout, stderr = io.StringIO(), io.StringIO()
    with (
        warnings.catch_warnings(record=True) as caught,
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        warnings.simplefilter("always")
        try:
            manifest = json.loads(args.manifest.read_text())
            if args.preflight_only:
                report = {
                    "status": "passed",
                    "source_sha256": validate_source(args.source_root, manifest),
                }
            else:
                negative = negative_control(args.source_root, args.manifest)
                report = run_probe(args.source_root, manifest, args.output)
                report["changed_source_negative_control"] = negative
            report["manifest_sha256"] = sha256(args.manifest.read_bytes())
        except Exception as error:
            report["status"] = "failed"
            report["error"] = type(error).__name__ + ": " + str(error)
            if not args.preflight_only:
                report["traceback"] = traceback.format_exc()
    report["torchsynth_imported"] = "torchsynth" in sys.modules
    report["warnings"] = [
        {
            "category": item.category.__name__,
            "message": str(item.message),
            "file": Path(item.filename).name,
            "line": item.lineno,
        }
        for item in caught
    ]
    report["stdout"] = stdout.getvalue()
    report["stderr"] = stderr.getvalue()
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
