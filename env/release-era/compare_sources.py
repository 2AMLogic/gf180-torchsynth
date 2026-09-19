"""Controlled selected-commit/v1.0.2 comparison in the release-era CPU image."""

import argparse
import contextlib
import datetime
import difflib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import warnings

from probe import identity, json_bytes, sha256, validate_source

HERE = Path(__file__).resolve().parent
PROBES = ("normalization-off", "normalization-on", "noise-bearing", "global-39942")
INPUTS = ("configuration", "normalized", "physical", "noise_sha256")


def load_manifest():
    return json.loads((HERE / "source-comparison.json").read_text())


def tree_identity(root):
    """Hash every exported file, including symlink targets and unexpected files."""
    files = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            files[name] = "symlink:" + os.readlink(path)
        elif path.is_file():
            files[name] = sha256(path.read_bytes())
    return {"tree_sha256": sha256(json_bytes(files)), "file_count": len(files)}


def validate_tree(root, expected):
    observed = tree_identity(root)
    if any(observed[key] != expected[key] for key in observed):
        raise ValueError("source tree mismatch (changed, missing or additional file)")
    return observed


def validate_patch(patch):
    if (
        patch["path"] != "torchsynth/synth.py"
        or patch["before"]
        != "from pytorch_lightning.core.lightning import LightningModule\n"
        or patch["after"] != "from lightning import LightningModule\n"
        or sha256(patch["unified_diff"].encode()) != patch["sha256"]
    ):
        raise ValueError("unapproved patch")


def prepare_release(directory):
    manifest = load_manifest()
    release = manifest["release"]
    archive = directory / "release.tar.gz"
    urllib.request.urlretrieve(release["archive_url"], archive)
    if sha256(archive.read_bytes()) != release["archive_sha256"]:
        raise ValueError("release archive hash mismatch")
    # Extraction is permitted only after verifying these exact pinned bytes.
    with tarfile.open(archive) as bundle:
        bundle.extractall(
            directory, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {})
        )
    root = directory / ("torchsynth-" + release["commit"])
    validate_tree(root, release)
    return root


def patched_release(root, destination, manifest):
    validate_tree(root, manifest["release"])
    patch = manifest["patch"]
    validate_patch(patch)
    shutil.copytree(root, destination, symlinks=True)
    path = destination / patch["path"]
    original = path.read_bytes()
    if original.count(patch["before"].encode()) != 1:
        raise ValueError("unapproved patch input")
    changed = original.replace(patch["before"].encode(), patch["after"].encode())
    actual_patch = "".join(
        difflib.unified_diff(
            original.decode().splitlines(True),
            changed.decode().splitlines(True),
            fromfile="a/" + patch["path"],
            tofile="b/" + patch["path"],
            n=0,
        )
    )
    if (
        actual_patch != patch["unified_diff"]
        or sha256(changed) != patch["patched_file_sha256"]
    ):
        raise ValueError("unapproved patch result")
    path.write_bytes(changed)
    expected = dict(manifest["release"], tree_sha256=patch["patched_tree_sha256"])
    return validate_tree(destination, expected)


def compare_samples(left, right):
    if not left or len(left) % 4 or len(left) != len(right):
        raise ValueError("empty, malformed or unequal float32 sample lengths")
    a = [x[0] for x in struct.iter_unpack("<f", left)]
    b = [x[0] for x in struct.iter_unpack("<f", right)]
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
        "rms_difference": math.sqrt(math.fsum(v * v for v in differences) / len(a)),
    }


def input_differences(left, right):
    return [key for key in INPUTS if json_bytes(left[key]) != json_bytes(right[key])]


def require_probe(name, peak, noise_peak):
    if not math.isfinite(peak) or peak <= 0:
        raise ValueError("silent or nonfinite probe")
    if name == "normalization-off" and peak > 1:
        raise ValueError("normalization-off did not exercise the bypass branch")
    if name == "normalization-on" and peak <= 1:
        raise ValueError("normalization-on did not exercise the normalization branch")
    if name == "noise-bearing" and (not math.isfinite(noise_peak) or noise_peak <= 0):
        raise ValueError("noise-bearing probe has no noise contribution")


def render(root, side, output, preflight_only):
    manifest = load_manifest()
    original = validate_tree(root, manifest[side])
    with tempfile.TemporaryDirectory() as directory:
        if side == "release":
            source = Path(directory) / "patched"
            effective = patched_release(root, source, manifest)
        else:
            source, effective = root, original
            validate_source(source, json.loads((HERE / "upstream.json").read_text()))
        gate = {
            "original": original,
            "effective": effective,
            "validated_before_import": True,
        }
        if preflight_only:
            return {"status": "passed", "source_gate": gate}
        if any(n == "torchsynth" or n.startswith("torchsynth.") for n in sys.modules):
            raise ValueError("TorchSynth already imported before validation")
        sys.path.insert(0, str(source))
        return render_validated(source, side, output, gate)


def render_validated(source, side, output, gate):
    import numpy as np
    import torch
    from torchsynth.config import SynthConfig, check_for_reproducibility
    from torchsynth.synth import Voice
    from torchsynth.util import normalize_if_clipping

    if (
        Path(sys.modules["torchsynth.synth"].__file__).resolve()
        != (source / "torchsynth/synth.py").resolve()
    ):
        raise ValueError("import did not use the validated source")
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    check_for_reproducibility()
    configuration = dict(
        batch_size=32,
        sample_rate=44100,
        control_rate=441,
        buffer_size_seconds=4.0,
        reproducible=True,
        no_grad=True,
    )
    expected_names = {
        ".".join(p["name"][:2])
        for p in json.loads(
            (source / "torchsynth/nebulae/voice/default.json").read_text()
        )
    }
    output.mkdir(parents=True, exist_ok=True)
    sounds = []
    for name in PROBES:
        index = 39942 if name == "global-39942" else 0
        coordinates = identity(index)
        slot = coordinates["render_slot"]
        voice = Voice(SynthConfig(**configuration), nebula="default").cpu().eval()
        voice.randomize(seed=coordinates["render_batch_index"])
        overrides = {}
        if name != "global-39942":
            level = 1.0 if name == "normalization-on" else 0.2
            overrides = {"mixer." + n: level for n in ("vco_1", "vco_2", "noise")}
            if name == "noise-bearing":
                overrides.update({"mixer.vco_1": 0.0, "mixer.vco_2": 0.0})
            voice.set_parameters(
                {
                    tuple(k.split(".")): torch.full((32,), v)
                    for k, v in overrides.items()
                }
            )
        normalized, physical = {}, {}
        for key, parameter in voice.get_parameters(include_frozen=True).items():
            normalized[".".join(key)] = float(parameter.detach()[slot])
            physical[".".join(key)] = float(parameter.from_0to1().detach()[slot])
        if set(normalized) != expected_names or len(normalized) != 78:
            raise ValueError("named parameter set mismatch")
        # Validate names and all values before rendering; serialization rejects NaN.
        json_bytes([normalized, physical])
        captured = {}

        def capture_normalization(frame, event, arg):
            if event == "call" and frame.f_code is normalize_if_clipping.__code__:
                captured["pre_normalization"] = (
                    frame.f_locals["signal"][slot].detach().clone()
                )

        def capture_noise(module, args, result):
            captured["noise"] = result[slot].detach().clone()

        def capture_mixer(module, args):
            captured["noise_contribution"] = (
                (args[2][slot] * module.p("noise")[slot]).detach().clone()
            )

        voice.noise.register_forward_hook(capture_noise)
        voice.mixer.register_forward_pre_hook(capture_mixer)
        # A call profiler observes the unmodified function's actual input. It never
        # replaces normalize_if_clipping or changes tensors/source/returned values.
        sys.setprofile(capture_normalization)
        try:
            with torch.inference_mode():
                captured["audio"] = voice.output()[slot].detach().clone()
        finally:
            sys.setprofile(None)
        artifacts = {}
        for kind, tensor in captured.items():
            samples = tensor.cpu().contiguous().numpy().astype("<f4", copy=False)
            if samples.shape != (176400,) or not np.isfinite(samples).all():
                raise ValueError("incorrect sample count or nonfinite " + kind)
            data = samples.tobytes()
            filename = name + "." + kind + ".f32le"
            (output / filename).write_bytes(data)
            artifacts[kind] = {
                "file": filename,
                "sha256": sha256(data),
                "peak": float(np.abs(samples).max()),
                "samples": len(samples),
            }
        peak = artifacts["pre_normalization"]["peak"]
        require_probe(name, peak, artifacts["noise_contribution"]["peak"])
        sounds.append(
            {
                "name": name,
                "identity": coordinates,
                "physical_overrides": overrides,
                "configuration": dict(
                    configuration,
                    nebula="default",
                    noise_seed=13,
                    device="cpu",
                    dtype="float32",
                    postprocessing="none",
                ),
                "normalized": normalized,
                "physical": physical,
                "noise_sha256": artifacts["noise"]["sha256"],
                "artifacts": artifacts,
                "normalization_applied": peak > 1.0,
            }
        )
    return {
        "status": "passed",
        "side": side,
        "source_gate": gate,
        "rng_sentinel": "passed",
        "sounds": sounds,
        "runtime": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "torch": torch.__version__,
            "threads": torch.get_num_threads(),
            "interop_threads": torch.get_num_interop_threads(),
            "packages": dict(
                sorted(
                    (d.metadata["Name"], d.version)
                    for d in importlib.metadata.distributions()
                )
            ),
        },
    }


def negative_control(root):
    with tempfile.TemporaryDirectory() as directory:
        altered = Path(directory) / "source"
        shutil.copytree(root, altered, symlinks=True)
        with (altered / "torchsynth/config.py").open("a") as handle:
            handle.write(
                "\nraise RuntimeError('non-approved source must never import')\n"
            )
        result = subprocess.run(
            [
                sys.executable,
                str(HERE / "compare_sources.py"),
                "render",
                "--side",
                "release",
                "--source-root",
                str(altered),
                "--preflight-only",
            ],
            capture_output=True,
            text=True,
        )
        report = json.loads(result.stdout)
        if (
            result.returncode != 1
            or report["torchsynth_imported"]
            or "source tree mismatch" not in report.get("error", "")
        ):
            raise ValueError("changed source was not rejected before import")
        return {
            "status": "passed",
            "exit_code": result.returncode,
            "report": report,
            "stderr": result.stderr,
        }


def compare_runs(output):
    (output / "source-equivalence.json").unlink(missing_ok=True)
    runs = [
        json.loads((output / (side + ".json")).read_text())
        for side in ("selected", "release")
    ]
    if any(r["status"] != "passed" for r in runs):
        raise ValueError("source render failed; inspect individual reports")
    if runs[0]["runtime"] != runs[1]["runtime"]:
        raise ValueError("runtime mismatch")
    for run in runs:
        if tuple(s["name"] for s in run["sounds"]) != PROBES:
            raise ValueError("missing, duplicate or reordered probe")
    comparisons = []
    for left, right in zip(runs[0]["sounds"], runs[1]["sounds"]):
        differences = input_differences(left, right)
        item = {
            "name": left["name"],
            "input_differences": differences,
            "comparisons": {},
        }
        # Do not judge audio unless the actual named inputs/noise/configuration agree.
        if not differences:
            for kind in ("noise", "pre_normalization", "noise_contribution", "audio"):
                raw = []
                for side, record in zip(("selected", "release"), (left, right)):
                    artifact = record["artifacts"][kind]
                    path = Path(artifact["file"])
                    if path.name != str(path):
                        raise ValueError("nonlocal artifact filename")
                    data = (output / side / path).read_bytes()
                    if sha256(data) != artifact["sha256"] or len(data) != 176400 * 4:
                        raise ValueError("artifact hash/length mismatch: " + str(path))
                    raw.append(data)
                item["comparisons"][kind] = compare_samples(*raw)
        item["status"] = (
            "passed"
            if not differences
            and all(c["equal_bytes"] for c in item["comparisons"].values())
            else "failed"
        )
        comparisons.append(item)
    passed = all(c["status"] == "passed" for c in comparisons)
    for side, run in zip(("selected", "release"), runs):
        run["container_stderr"] = (output / (side + ".stderr")).read_text()
    report = {
        "schema_version": 1,
        "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "passed" if passed else "failed",
        "command": "./env/release-era/compare_sources.sh",
        "source_manifest": load_manifest(),
        "fresh_container_count": 2,
        "image_id": (output / "image.id").read_text().strip(),
        "image_platform": (output / "image-platform.txt").read_text().strip(),
        "docker_server": (output / "docker-server.txt").read_text().strip(),
        "host": {"system": platform.system(), "machine": platform.machine()},
        "definition_sha256": {
            p.name: sha256(p.read_bytes())
            for p in sorted(HERE.iterdir())
            if p.is_file()
        },
        "build_warnings": [
            line
            for line in (output / "build.log").read_text().splitlines()
            if "warning" in line.lower()
        ],
        "changed_source_negative_control": json.loads(
            (output / "negative.json").read_text()
        ),
        "comparisons": comparisons,
        "runs": runs,
        "conclusion": "Supports DR-0001 Voice compatibility for these probes in this environment."
        if passed
        else "Does not establish DR-0001 compatibility; inspect differences.",
        "limitations": [
            "No perceptual inference, canonical-runtime ratification, cross-environment or hardware claim.",
            "The 32-row reproducible batch selects qualification fixtures, not hardware batching.",
            "Only four selected rows compared; scalar/batch qualification remains downstream.",
            "Traces compared: normalization input, raw noise and weighted noise contribution. Other intermediate traces unavailable in this record.",
            "Normalization remains the upstream conditional operation; no rescaling/alignment/postprocessing.",
        ],
    }
    (output / "source-equivalence.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    if not passed:
        raise ValueError("source comparison failed; inspect source-equivalence.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "render", "negative", "compare"))
    parser.add_argument("--side", choices=("selected", "release"))
    parser.add_argument("--source-root", type=Path, default=Path("/opt/torchsynth"))
    parser.add_argument("--output", type=Path, default=Path("/output"))
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        print(prepare_release(args.output))
        return 0
    if args.action == "compare":
        compare_runs(args.output)
        return 0
    if args.action == "negative":
        print(json.dumps(negative_control(args.source_root), indent=2, sort_keys=True))
        return 0
    stdout, stderr = io.StringIO(), io.StringIO()
    with (
        warnings.catch_warnings(record=True) as caught,
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        warnings.simplefilter("always")
        try:
            report = render(
                args.source_root, args.side, args.output, args.preflight_only
            )
        except Exception as error:
            report = {
                "status": "failed",
                "error": type(error).__name__ + ": " + str(error),
            }
    report.update(torchsynth_imported="torchsynth" in sys.modules)
    report["warnings"] = [
        {
            "category": w.category.__name__,
            "message": str(w.message),
            "file": Path(w.filename).name,
            "line": w.lineno,
        }
        for w in caught
    ]
    report.update(stdout=stdout.getvalue(), stderr=stderr.getvalue())
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
