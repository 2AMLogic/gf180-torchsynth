"""Qualify resolved scalar replay against the pinned 32-row Voice (Python 3.9+)."""

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
import struct
import sys
import traceback
import uuid
import warnings
from pathlib import Path

from probe import identity, json_bytes, sha256, validate_source

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ENVELOPE_PATH = REPO / "spec/reference/scalar-envelope-v1.json"
ENVELOPE_REFERENCES = REPO / "sim/reference/scalar-sentinel-v1"
ROUTES = ("vco_1_pitch", "vco_1_amp", "vco_2_pitch", "vco_2_amp", "noise_amp")
ENVELOPES = (
    "lfo_1_rate_adsr",
    "lfo_2_rate_adsr",
    "lfo_1_amp_adsr",
    "lfo_2_amp_adsr",
    "adsr_1",
    "adsr_2",
)


def require_provenance(expected, actual):
    if expected != actual:
        raise ValueError("provenance mismatch")


def require_named(expected, actual):
    if set(expected) != set(actual):
        raise ValueError("input.normalized: missing or extra names")
    for name in sorted(expected):
        for value in (expected[name], actual[name]):
            if (
                type(value) not in (float, int)
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("input.normalized: invalid value " + name)
            if struct.unpack("<f", struct.pack("<f", value))[0] != value:
                raise ValueError("input.normalized: not exact binary32 " + name)
        if struct.pack("<f", expected[name]) != struct.pack("<f", actual[name]):
            raise ValueError("input.normalized: changed value " + name)


def require_noise(expected, actual, expected_slot, actual_slot, count=176400):
    if (
        len(expected) != count * 4
        or len(actual) != count * 4
        or expected != actual
        or expected_slot != actual_slot
    ):
        raise ValueError("input.noise: selected slot, bytes or sample count mismatch")


def capture_manifest():
    """Ordered observations of the pinned graph; gain is explicitly derived."""
    return [
        ("input.normalized", [78]),
        ("input.noise", [176400]),
        ("physical.parameters", [78]),
        ("keyboard.midi_f0", [1]),
        ("keyboard.duration", [1]),
        *[(name + ".output", [1764]) for name in ENVELOPES[:4]],
        ("lfo_1.raw", [1764]),
        ("lfo_1.post_control_vca", [1764]),
        ("lfo_2.raw", [1764]),
        ("lfo_2.post_control_vca", [1764]),
        *[(name + ".output", [1764]) for name in ENVELOPES[4:]],
        *[("mod_matrix." + name, [1764]) for name in ROUTES],
        ("control_upsample.vco_1_pitch", [176400]),
        ("vco_1.raw", [176400]),
        ("control_upsample.vco_1_amp", [176400]),
        ("vco_1.post_vca", [176400]),
        ("control_upsample.vco_2_pitch", [176400]),
        ("vco_2.raw", [176400]),
        ("control_upsample.vco_2_amp", [176400]),
        ("vco_2.post_vca", [176400]),
        ("noise.raw", [176400]),
        ("control_upsample.noise_amp", [176400]),
        ("noise.post_vca", [176400]),
        ("mixer.pre_normalization", [176400]),
        ("mixer.peak", [1]),
        ("mixer.gain", [1]),
        ("mixer.output", [176400]),
        ("audio.final", [176400]),
    ]


def require_trace(trace):
    if [(r["name"], r["shape"]) for r in trace] != capture_manifest():
        raise ValueError(
            "trace manifest mismatch: missing, extra, reordered or wrong shape"
        )


def differences(left, right):
    if not left or len(left) % 4 or len(left) != len(right):
        raise ValueError("empty, malformed or unequal float32 lengths")
    if left == right:
        if not all(math.isfinite(v[0]) for v in struct.iter_unpack("<f", left)):
            raise ValueError("nonfinite sample")
        return {
            "equal_bytes": True,
            "samples": len(left) // 4,
            "first_different_byte": None,
            "first_different_sample": None,
            "first_values": None,
            "max_abs_difference": 0.0,
            "mean_abs_difference": 0.0,
            "rms_difference": 0.0,
        }
    a = [v[0] for v in struct.iter_unpack("<f", left)]
    b = [v[0] for v in struct.iter_unpack("<f", right)]
    if not all(math.isfinite(v) for v in a + b):
        raise ValueError("nonfinite sample")
    delta = [abs(x - y) for x, y in zip(a, b)]
    first_byte = next((i for i, (x, y) in enumerate(zip(left, right)) if x != y), None)
    first = None if first_byte is None else first_byte // 4
    return {
        "equal_bytes": left == right,
        "samples": len(a),
        "first_different_byte": first_byte,
        "first_different_sample": first,
        "first_values": None if first is None else [a[first], b[first]],
        "max_abs_difference": max(delta),
        "mean_abs_difference": math.fsum(delta) / len(a),
        "rms_difference": math.sqrt(math.fsum(v * v for v in delta) / len(a)),
    }


def read_artifact(directory, record):
    name = record["file"]
    if Path(name).name != name or name in (".", ".."):
        raise ValueError("nonlocal artifact filename")
    data = (directory / name).read_bytes()
    if sha256(data) != record["sha256"] or len(data) != math.prod(record["shape"]) * 4:
        raise ValueError("artifact hash/count mismatch: " + name)
    return data


def load_plan(sentinel=False):
    plan = json.loads((HERE / "scalar-cases.json").read_text())
    for name, expected in plan["input_sha256"].items():
        if sha256((REPO / name).read_bytes()) != expected:
            raise ValueError("stale preregistered input: " + name)
    if plan["schema_version"] != 1 or plan["capture_version"] != "scalar-capture-v1":
        raise ValueError("unknown scalar protocol")
    cases = [c for c in plan["cases"] if not sentinel or c["id"] in plan["sentinel"]]
    return plan, cases


def provenance(source_root, plan):
    manifest = json.loads((REPO / "spec/reference/upstream.json").read_text())
    require_provenance(plan["source_commit"], manifest["target_commit"])
    source = validate_source(source_root, manifest)
    # Also retain all importable package files, including signal.py / __init__.py.
    package = {
        p.relative_to(source_root).as_posix(): sha256(p.read_bytes())
        for p in sorted((source_root / "torchsynth").rglob("*"))
        if p.is_file() and p.suffix in (".py", ".json")
    }
    if sha256(json_bytes(package)) != plan["package_tree_sha256"]:
        raise ValueError(
            "source package tree mismatch (changed, extra or missing file)"
        )
    files = (
        list(plan["input_sha256"])
        + [
            "env/release-era/" + name
            for name in (
                "qualify_scalar.py",
                "qualify_scalar.sh",
                "scalar-cases.json",
                "probe.py",
                "Dockerfile",
                "requirements.lock",
            )
        ]
        + ["uv.lock"]
        + ["spec/reference/scalar-envelope-v1.json"]
    )
    return {
        "source_commit": plan["source_commit"],
        "source_sha256": source,
        "package_sha256": package,
        "definition_sha256": {
            name: sha256((REPO / name).read_bytes()) for name in files
        },
    }


def runtime_identity(torch):
    packages = dict(
        sorted(
            (d.metadata["Name"].lower().replace("_", "-"), d.version)
            for d in importlib.metadata.distributions()
        )
    )
    lock = (HERE / "requirements.lock").read_text()
    pinned = dict(
        re.findall(r"^([\w-]+)(?:\[[^\]]+\])?==([^\s\\]+)", lock, re.MULTILINE)
    )
    pinned = {k.lower().replace("_", "-"): v for k, v in pinned.items()}
    pinned["torch"] = "1.12.1+cpu"  # direct wheel URL in the normative lock
    mismatches = {
        k: [v, packages.get(k)] for k, v in pinned.items() if packages.get(k) != v
    }
    if (
        mismatches
        or platform.python_version() != "3.9.13"
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise ValueError(
            "unqualified runtime; require release-era linux/amd64 Python 3.9.13: "
            + str(mismatches)
        )
    return {
        "name": "release-era-linux-amd64",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "torch_build": torch.__config__.show(),
        "device": "cpu",
        "dtype": "float32",
        "threads": torch.get_num_threads(),
        "interop_threads": torch.get_num_interop_threads(),
        "thread_environment": {
            k: os.environ.get(k)
            for k in (
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        },
        "math_environment": {
            k: os.environ.get(k)
            for k in (
                "ATEN_CPU_CAPABILITY",
                "MKL_ENABLE_INSTRUCTIONS",
                "ONEDNN_MAX_CPU_ISA",
                "MKL_CBWR",
            )
        },
        "lock_versions_verified": True,
    }


def named_values(voice, slot, physical=False):
    return {
        ".".join(key): float((p.from_0to1() if physical else p.detach())[slot].item())
        for key, p in voice.get_parameters(include_frozen=True).items()
    }


def assign_named(voice, normalized, names, torch, freeze=False):
    require_named(dict.fromkeys(names, 0.0), dict.fromkeys(normalized, 0.0))
    require_named(normalized, normalized)
    for name, value in normalized.items():
        module, parameter = name.split(".", 1)
        getattr(voice, module).set_parameter_0to1(
            parameter, torch.full((int(voice.batch_size),), value, dtype=torch.float32)
        )
        if freeze:
            getattr(voice, module).get_parameter(parameter).frozen = True
    require_named(normalized, named_values(voice, 0))


def tensor_bytes(tensor, torch):
    if (
        tensor.dtype != torch.float32
        or tensor.device.type != "cpu"
        or sys.byteorder != "little"
    ):
        raise ValueError("expected original little-endian CPU binary32 tensor")
    if not torch.isfinite(tensor).all():
        raise ValueError("nonfinite tensor")
    return tensor.detach().contiguous().numpy().tobytes()


class Capture:
    """Passive forward hooks + a Python return observer; never replace graph code."""

    def __init__(self, voice, slot, output, case_id, torch):
        self.voice, self.slot, self.output, self.case_id, self.torch = (
            voice,
            slot,
            output,
            case_id,
            torch,
        )
        self.records, self.handles, self.counts = [], [], {}

    def add(self, name, tensor):
        expected = dict(capture_manifest())[name]
        if tensor.ndim == 0 and expected == [1]:
            tensor = tensor.reshape(1)
        if list(tensor.shape) != expected:
            raise ValueError("trace manifest shape mismatch: " + name)
        data = tensor_bytes(tensor, self.torch)
        filename = self.case_id + "." + name + ".f32le"
        (self.output / filename).write_bytes(data)
        self.records.append(
            {
                "name": name,
                "shape": list(tensor.shape),
                "file": filename,
                "sha256": sha256(data),
            }
        )

    def hook(self, module_name):
        def observe(module, inputs, value):
            count = self.counts.get(module_name, 0)
            self.counts[module_name] = count + 1
            if module_name == "keyboard":
                for name, item in zip(("keyboard.midi_f0", "keyboard.duration"), value):
                    self.add(name, item[self.slot])
            elif module_name == "mod_matrix":
                for name, item in zip(ROUTES, value):
                    self.add("mod_matrix." + name, item[self.slot])
            else:
                if module_name == "control_vca":
                    name = ("lfo_1.post_control_vca", "lfo_2.post_control_vca")[count]
                elif module_name == "control_upsample":
                    name = "control_upsample." + ROUTES[count]
                elif module_name == "vca":
                    name = ("vco_1.post_vca", "vco_2.post_vca", "noise.post_vca")[count]
                else:
                    suffix = (
                        ".raw"
                        if module_name in ("lfo_1", "lfo_2", "vco_1", "vco_2", "noise")
                        else ".output"
                    )
                    name = module_name + suffix
                self.add(name, value[self.slot])

        return observe

    def __enter__(self):
        from torchsynth import util

        if sys.getprofile() is not None:
            raise ValueError("another profiler is active")
        for name in (
            "keyboard",
            *ENVELOPES,
            "lfo_1",
            "lfo_2",
            "control_vca",
            "mod_matrix",
            "control_upsample",
            "vco_1",
            "vco_2",
            "noise",
            "vca",
            "mixer",
        ):
            self.handles.append(
                getattr(self.voice, name).register_forward_hook(self.hook(name))
            )

        def observe(frame, event, arg):
            if (
                event == "return"
                and frame.f_code is util.normalize_if_clipping.__code__
            ):
                peak = frame.f_locals["max_sample"][self.slot]
                self.add("mixer.pre_normalization", frame.f_locals["signal"][self.slot])
                self.add("mixer.peak", peak)
                # A derived diagnostic, since upstream divides directly by peak.
                self.add(
                    "mixer.gain",
                    self.torch.where(peak > 1, 1 / peak, self.torch.ones_like(peak)),
                )

        sys.setprofile(observe)
        return self

    def __exit__(self, *args):
        sys.setprofile(None)
        for handle in self.handles:
            handle.remove()


def directed_patch(case, directed):
    fixture = next(c for c in directed["cases"] if c["id"] == case["directed"])
    values = {name: pair["normalized"] for name, pair in directed["base"].items()}
    values.update(
        {name: pair["normalized"] for name, pair in fixture["overrides"].items()}
    )
    values.update(case.get("normalized_overrides", {}))
    return values


def require_execution(report, side, repeat=None, campaign=None):
    if report.get("side") != side:
        raise ValueError("execution role mismatch: expected " + side)
    execution = report.get("execution", {})
    try:
        for key in ("uuid", "campaign_id"):
            if str(uuid.UUID(execution[key], version=4)) != execution[key]:
                raise ValueError("noncanonical UUID")
        started = datetime.datetime.fromisoformat(execution["started_utc"])
        if (
            started.tzinfo is None
            or type(execution["pid"]) is not int
            or execution["pid"] <= 0
        ):
            raise ValueError("invalid process identity")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("missing or invalid execution identity") from error
    if type(execution.get("repeat")) is not int or execution["repeat"] not in (1, 2):
        raise ValueError("invalid execution repeat")
    if repeat is not None and execution["repeat"] != repeat:
        raise ValueError("execution repeat association mismatch")
    if campaign is not None and execution["campaign_id"] != campaign:
        raise ValueError("execution campaign association mismatch")
    return execution["uuid"]


def require_render(report, side):
    require_execution(report, side)
    plan, cases = load_plan()
    if (
        report.get("status") != "PASS"
        or report.get("mutation") is not None
        or report.get("capture_version") != plan["capture_version"]
        or report.get("rng_sentinel") != "PASS"
        or report.get("source_unchanged_after_render") is not True
    ):
        raise ValueError("an actual render failed or lacks required gates")
    definitions = {c["id"]: c for c in cases}
    seen = set()
    for case in report["cases"]:
        if case["id"] in seen or case["id"] not in definitions:
            raise ValueError("missing, duplicate or unknown render case")
        seen.add(case["id"])
        expected = definitions[case["id"]]
        if (
            type(case.get("execution_width")) is not int
            or case["execution_width"] != (32 if side == "canonical" else 1)
            or case.get("reproducible") is not (side == "canonical")
            or case["configuration"] != plan["configuration"]
        ):
            raise ValueError("execution configuration mismatch: " + side)
        require_provenance(expected, case["case_definition"])
        require_provenance(
            identity(expected["sound_index"]), case["corpus_coordinates"]
        )
        require_trace(case["traces"])
        final_audio = next(t for t in case["traces"] if t["name"] == "audio.final")
        if (
            case.get("passive_capture_invariant") is not True
            or case.get("no_hook_audio_sha256") != final_audio["sha256"]
        ):
            raise ValueError(
                "passive capture evidence missing, false or unbound: " + case["id"]
            )
    if not seen:
        raise ValueError("missing render cases")


def require_association(canonical, scalar, canonical_path):
    require_execution(
        scalar,
        "scalar",
        canonical["execution"]["repeat"],
        canonical["execution"]["campaign_id"],
    )
    if scalar.get("canonical_execution_uuid") != canonical["execution"][
        "uuid"
    ] or scalar.get("canonical_report_sha256") != sha256(canonical_path.read_bytes()):
        raise ValueError("canonical execution association mismatch")
    if scalar["execution"]["uuid"] == canonical["execution"]["uuid"]:
        raise ValueError("reused execution identity")


def run_render(args):
    plan, cases = load_plan(args.sentinel)
    before = provenance(args.source_root, plan)
    if any(
        name == "torchsynth" or name.startswith("torchsynth.") for name in sys.modules
    ):
        raise ValueError("TorchSynth imported before source gate")
    sys.path.insert(0, str(args.source_root))
    import numpy as np
    import torch
    from torchsynth.config import SynthConfig, check_for_reproducibility
    from torchsynth.synth import Voice

    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    runtime = runtime_identity(torch)
    check_for_reproducibility()
    inventory = json.loads(
        (REPO / "spec/reference/parameter-inventory-v1.json").read_text()
    )
    names = sorted(p["name"] for p in inventory["parameters"])
    if len(names) != 78 or len(set(names)) != 78:
        raise ValueError("inventory must contain 78 distinct names")
    directed = json.loads((REPO / "spec/reference/directed-voice-v1.json").read_text())
    canonical = None
    if args.side == "scalar":
        canonical = json.loads((args.canonical / "report.json").read_text())
        require_render(canonical, "canonical")
        require_execution(canonical, "canonical", args.repeat, args.campaign_id)
        args.canonical_execution_uuid = canonical["execution"]["uuid"]
        args.canonical_report_sha256 = sha256(
            (args.canonical / "report.json").read_bytes()
        )
        require_provenance(before, canonical["provenance"])
        require_provenance(runtime, canonical["runtime"])
        if [r["id"] for r in canonical["cases"]] != [c["id"] for c in cases]:
            raise ValueError("canonical case set/order mismatch")
    if args.mutation:
        cases = [c for c in cases if c["id"] == plan["controls"][args.mutation]["case"]]
    results = []
    args.output.mkdir(parents=True, exist_ok=True)
    for case in cases:
        coordinates = identity(case["sound_index"])
        batch = 32 if args.side == "canonical" else 1
        slot = coordinates["render_slot"] if batch == 32 else 0
        voice = (
            Voice(
                SynthConfig(
                    batch_size=batch, reproducible=batch == 32, **plan["configuration"]
                ),
                nebula="default",
            )
            .cpu()
            .eval()
        )
        with torch.no_grad():
            expected_noise = None
            if batch == 32:
                voice.randomize(seed=coordinates["render_batch_index"])
                if "directed" in case:
                    assign_named(
                        voice, directed_patch(case, directed), names, torch, freeze=True
                    )
                normalized = named_values(voice, slot)
            else:
                reference = next(c for c in canonical["cases"] if c["id"] == case["id"])
                require_provenance(case, reference["case_definition"])
                require_provenance(coordinates, reference["corpus_coordinates"])
                require_provenance(plan["configuration"], reference["configuration"])
                require_trace(reference["traces"])
                normalized = reference["normalized_by_name"]
                assign_named(voice, normalized, names, torch)
                if tensor_bytes(
                    torch.tensor([normalized[n] for n in names]), torch
                ) != read_artifact(args.canonical, reference["traces"][0]):
                    raise ValueError(
                        "input.normalized: named JSON differs from captured bytes"
                    )
                expected_noise = read_artifact(args.canonical, reference["traces"][1])
                if args.mutation != "wrong-noise":
                    # Exact copied selected stream; never re-seed to impersonate slot 6.
                    voice.noise.noise.copy_(
                        torch.from_numpy(
                            np.frombuffer(expected_noise, dtype="<f4").copy()
                        ).reshape(1, -1)
                    )
                if args.mutation == "wrong-parameter":
                    voice.keyboard.set_parameter_0to1(
                        "midi_f0",
                        torch.tensor(
                            [0.0 if normalized["keyboard.midi_f0"] != 0 else 1.0]
                        ),
                    )
                if args.mutation == "fresh-randomization":
                    voice.randomize(seed=0)
                if args.mutation:
                    actual_noise = tensor_bytes(voice.noise.noise[slot], torch)
                    (args.output / "actual-noise.f32le").write_bytes(actual_noise)
                    args.control_observation = {
                        "case": case["id"],
                        "runtime": runtime,
                        "provenance": before,
                        "expected_normalized": normalized,
                        "actual_normalized": named_values(voice, slot),
                        "expected_noise_sha256": sha256(expected_noise),
                        "actual_noise_sha256": sha256(actual_noise),
                        "actual_noise": {
                            "file": "actual-noise.f32le",
                            "shape": [176400],
                            "sha256": sha256(actual_noise),
                        },
                        "expected_noise_slot": coordinates["noise_slot"],
                        "actual_noise_slot": 0
                        if args.mutation == "wrong-noise"
                        else coordinates["noise_slot"],
                    }
            require_named(dict.fromkeys(names, 0.0), dict.fromkeys(normalized, 0.0))
            require_named(normalized, named_values(voice, slot))
            raw_noise = tensor_bytes(voice.noise.noise[slot], torch)
            require_noise(
                raw_noise if expected_noise is None else expected_noise,
                raw_noise,
                coordinates["noise_slot"],
                coordinates["noise_slot"],
            )
            physical = named_values(voice, slot, physical=True)
            capture = Capture(voice, slot, args.output, case["id"], torch)
            capture.add(
                "input.normalized", torch.tensor([normalized[n] for n in names])
            )
            capture.add("input.noise", voice.noise.noise[slot])
            capture.add(
                "physical.parameters", torch.tensor([physical[n] for n in names])
            )
            with capture:
                audio, _, is_train = (
                    voice(coordinates["render_batch_index"]) if batch == 32 else voice()
                )
            capture.add("audio.final", audio[slot])
            require_trace(capture.records)
            captured_audio = tensor_bytes(audio[slot], torch)
            plain, _, _ = (
                voice(coordinates["render_batch_index"]) if batch == 32 else voice()
            )
            if captured_audio != tensor_bytes(plain[slot], torch):
                raise ValueError("passive capture changed audio")
            require_named(normalized, named_values(voice, slot))
            require_noise(
                raw_noise,
                tensor_bytes(voice.noise.noise[slot], torch),
                coordinates["noise_slot"],
                coordinates["noise_slot"],
            )
            if batch == 32 and bool(is_train[slot]) != coordinates["is_train"]:
                raise ValueError("corpus train/test identity mismatch")
            if batch == 1 and is_train is not None:
                raise ValueError("scalar forward unexpectedly used a batch index")
        trace_map = {r["name"]: r for r in capture.records}
        peak = struct.unpack("<f", read_artifact(args.output, trace_map["mixer.peak"]))[
            0
        ]
        branch = "applied" if peak > 1 else "bypassed"
        if case.get("normalization", branch) != branch or peak <= 0:
            raise ValueError("normalization case did not exercise preregistered branch")
        results.append(
            {
                "id": case["id"],
                "case_definition": case,
                "corpus_coordinates": coordinates,
                "is_unmodified_corpus_sound": "directed" not in case,
                "execution_width": batch,
                "reproducible": batch == 32,
                "configuration": plan["configuration"],
                "normalized_by_name": normalized,
                "parameter_order": names,
                "selected_noise_sha256": sha256(raw_noise),
                "traces": capture.records,
                "passive_capture_invariant": True,
                "no_hook_audio_sha256": sha256(captured_audio),
                "normalization": {"branch": branch, "peak": peak},
            }
        )
        print(case["id"] + " rendered: " + args.side, file=sys.stderr)
        del voice, audio, plain, capture
    require_provenance(before, provenance(args.source_root, plan))
    return {
        "status": "PASS",
        "side": args.side,
        "provenance": before,
        "runtime": runtime,
        "rng_sentinel": "PASS",
        "source_unchanged_after_render": True,
        "capture_version": plan["capture_version"],
        "cases": results,
    }


def compare_run(canonical_dir, scalar_dir):
    left, right = [
        json.loads((p / "report.json").read_text()) for p in (canonical_dir, scalar_dir)
    ]
    require_render(left, "canonical")
    require_render(right, "scalar")
    require_association(left, right, canonical_dir / "report.json")
    require_provenance(left["provenance"], right["provenance"])
    require_provenance(left["runtime"], right["runtime"])
    if [r["id"] for r in left["cases"]] != [r["id"] for r in right["cases"]]:
        raise ValueError("case set/order mismatch")
    comparisons = []
    for a, b in zip(left["cases"], right["cases"]):
        for key in (
            "case_definition",
            "corpus_coordinates",
            "configuration",
            "parameter_order",
        ):
            require_provenance(a[key], b[key])
        require_named(a["normalized_by_name"], b["normalized_by_name"])
        require_trace(a["traces"])
        require_trace(b["traces"])
        metrics = []
        for x, y in zip(a["traces"], b["traces"]):
            raw_a, raw_b = read_artifact(canonical_dir, x), read_artifact(scalar_dir, y)
            if x["name"] == "input.noise":
                require_noise(
                    raw_a,
                    raw_b,
                    a["corpus_coordinates"]["noise_slot"],
                    b["corpus_coordinates"]["noise_slot"],
                )
            metrics.append(
                dict(
                    differences(raw_a, raw_b),
                    seam=x["name"],
                    canonical_sha256=x["sha256"],
                    scalar_sha256=y["sha256"],
                )
            )
        first = next((m for m in metrics if not m["equal_bytes"]), None)
        comparisons.append(
            {
                "id": a["id"],
                "byte_equivalence": "PASS" if first is None else "FAIL",
                "first_divergence": first,
                "seams": metrics,
                "canonical": a,
                "scalar": b,
            }
        )
    return left, right, comparisons


def require_control(report, name, definition, canonical, canonical_dir, control_dir):
    if report.get("mutation") != name:
        raise ValueError("control mutation association mismatch")
    require_association(canonical, report, canonical_dir / "report.json")
    if report.get("status") != "NO_VERDICT" or not report.get("error", "").startswith(
        "ValueError: " + definition["expected_seam"] + ":"
    ):
        raise ValueError("start-red control failed at unexpected seam: " + name)
    observed = report.get("control_observation", {})
    for key in ("provenance", "runtime"):
        require_provenance(canonical[key], observed.get(key))
    if observed.get("case") != definition["case"]:
        raise ValueError("control case association mismatch")
    case = next(c for c in canonical["cases"] if c["id"] == definition["case"])
    expected, actual = case["normalized_by_name"], observed.get("actual_normalized", {})
    require_named(expected, observed.get("expected_normalized", {}))
    require_named(dict.fromkeys(expected, 0.0), dict.fromkeys(actual, 0.0))
    require_named(actual, actual)
    changed = [
        key
        for key in expected
        if struct.pack("<f", expected[key]) != struct.pack("<f", actual[key])
    ]
    original_noise = read_artifact(canonical_dir, case["traces"][1])
    if (
        observed["actual_noise"].get("shape") != [176400]
        or type(observed["actual_noise"]["shape"][0]) is not int
    ):
        raise ValueError("control noise must contain exactly 176400 float32 samples")
    actual_noise = read_artifact(control_dir, observed["actual_noise"])
    differences(actual_noise, actual_noise)  # validate every retained float32 is finite
    if (
        observed.get("expected_noise_sha256") != sha256(original_noise)
        or observed.get("actual_noise_sha256") != sha256(actual_noise)
        or observed.get("expected_noise_slot")
        != case["corpus_coordinates"]["noise_slot"]
    ):
        raise ValueError("control noise provenance mismatch")
    if name == "wrong-noise":
        require_named(expected, actual)
        wrong_slot = next(
            c
            for c in canonical["cases"]
            if c["id"] == definition["actual_noise_reference_case"]
        )
        if wrong_slot["corpus_coordinates"]["noise_slot"] != 0:
            raise ValueError("control wrong-noise reference is not canonical slot 0")
        require_noise(
            read_artifact(canonical_dir, wrong_slot["traces"][1]),
            actual_noise,
            0,
            observed.get("actual_noise_slot"),
        )
        if actual_noise == original_noise or observed.get("actual_noise_slot") != 0:
            raise ValueError("control noise mutation was not observed")
    else:
        require_noise(
            original_noise,
            actual_noise,
            observed["expected_noise_slot"],
            observed.get("actual_noise_slot"),
        )
        if name == "wrong-parameter":
            endpoint = 0.0 if expected["keyboard.midi_f0"] != 0 else 1.0
            if (
                changed != ["keyboard.midi_f0"]
                or actual["keyboard.midi_f0"] != endpoint
            ):
                raise ValueError("control wrong-parameter mutation was not observed")
        elif len(changed) < 2:
            raise ValueError("control fresh-randomization mutation was not observed")


def aggregate(output, sentinel, *, write=True):
    plan, cases = load_plan(sentinel)
    runs = [
        compare_run(output / f"run-{n}" / "canonical", output / f"run-{n}" / "scalar")
        for n in (1, 2)
    ]
    campaign = (output / "campaign.id").read_text().strip()
    executions = {}
    directories = []
    for repeat, run in enumerate(runs, 1):
        for side, report in zip(("canonical", "scalar"), run[:2]):
            key = f"run-{repeat}/{side}"
            identity_id = require_execution(report, side, repeat, campaign)
            if identity_id in executions:
                raise ValueError("reused execution identity")
            executions[identity_id] = dict(report["execution"], path=key, side=side)
            directories.append((output / key).resolve())
    if len(set(directories)) != 4:
        raise ValueError("reused render directory")
    for name, expected in runs[0][0]["provenance"]["definition_sha256"].items():
        if sha256((REPO / name).read_bytes()) != expected:
            raise ValueError("stale run definition provenance: " + name)
    for side in (0, 1):
        require_provenance(runs[0][side]["provenance"], runs[1][side]["provenance"])
        require_provenance(runs[0][side]["runtime"], runs[1][side]["runtime"])
        if runs[0][side]["cases"] != runs[1][side]["cases"]:
            raise ValueError("fresh-process repeat differed")
    if [c["id"] for c in runs[0][2]] != [c["id"] for c in cases]:
        raise ValueError("missing required case")
    controls = {}
    for name, definition in plan["controls"].items():
        report = json.loads((output / "controls" / name / "report.json").read_text())
        require_control(
            report,
            name,
            definition,
            runs[0][0],
            output / "run-1/canonical",
            output / "controls" / name,
        )
        identity_id = require_execution(report, "scalar", 1, campaign)
        if identity_id in executions:
            raise ValueError("reused control execution identity")
        executions[identity_id] = dict(
            report["execution"], path="controls/" + name, side="scalar", mutation=name
        )
        controls[name] = {
            "expected_seam": definition["expected_seam"],
            "observed": report,
            "detected": True,
            "expected_exit_code": 1,
        }
    equal = all(c["byte_equivalence"] == "PASS" for c in runs[0][2])
    report = {
        "schema_version": 1,
        "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),  # noqa: UP017 -- Python 3.9 runtime
        "status": "PASS",
        "scope": "sentinel" if sentinel else "full-preregistered-cases",
        "byte_equivalence": "PASS" if equal else "FAIL",
        "batch_1_oracle": "diagnostic",
        "runtime_reconciliation": "pending issue 12; no chosen-runtime bridge qualification",
        "command": "./env/release-era/qualify_scalar.sh"
        + (" --sentinel" if sentinel else "")
        + (
            " --mkl-compatible"
            if runs[0][0]["runtime"].get("math_environment", {}).get("MKL_CBWR")
            == "COMPATIBLE"
            else ""
        ),
        "provenance": runs[0][0]["provenance"],
        "runtime": runs[0][0]["runtime"],
        "runtime_sha256": sha256(
            json.dumps(runs[0][0]["runtime"], sort_keys=True).encode()
        ),
        "image_id": (output / "image.id").read_text().strip(),
        "docker_server": (output / "docker-server.txt").read_text().strip(),
        "host": {"system": platform.system(), "machine": platform.machine()},
        "fresh_render_processes": 4,
        "execution_records": list(executions.values()),
        "campaign_id": campaign,
        "fresh_process_repeats_equal": True,
        "capture_manifest": [{"name": n, "shape": s} for n, s in capture_manifest()],
        "comparisons": runs[0][2],
        "negative_controls": controls,
        "run_report_sha256": {},
        "warnings": {},
        "limitations": [
            plan["scope"],
            plan["normalization_gain"],
            "PASS means apparatus, input gates and repeat checks passed; inspect byte_equivalence separately.",
            "No numerical tolerance, perceptual fidelity, fixed-point, RTL, physical validation or holdout qualification.",
            "Other runtimes are unrun by this probe and receive NO_VERDICT.",
        ],
    }
    for path in sorted(output.glob("run-*/*/report.json")):
        name = path.relative_to(output).as_posix()
        report["run_report_sha256"][name] = sha256(path.read_bytes())
        run = json.loads(path.read_text())
        report["warnings"][name] = {
            key: run[key] for key in ("warnings", "stdout", "stderr")
        }
    report["build_log_sha256"] = sha256((output / "build.log").read_bytes())
    report["build_warnings"] = [
        s
        for s in (output / "build.log").read_text().splitlines()
        if "warning" in s.lower()
    ]
    report["process_stderr"] = {
        p.name: {"sha256": sha256(p.read_bytes()), "text": p.read_text()}
        for p in sorted(output.glob("*.stderr"))
    }
    if write:
        (output / "scalar-execution.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
        print("Apparatus PASS; scalar byte equivalence " + report["byte_equivalence"])
    return report


def load_envelope(path=None):
    """Load and validate the declared sentinel reproducibility envelope.

    The census is data (DR-0009 amendment A1, issue 151): regions not named
    here stay platform-invariant and are asserted byte-exact; a region named
    here must hold its declared guarantee, and no comparison may relax a
    byte-exact assertion without a committed envelope change plus a DR
    amendment to this record.
    """
    envelope_path = Path(path) if path is not None else ENVELOPE_PATH
    data = json.loads(envelope_path.read_text())
    if data.get("schema_version") != 1:
        raise ValueError("unknown scalar envelope schema")
    sentinel_cases = set(json.loads((HERE / "scalar-cases.json").read_text())["sentinel"])
    regions = data.get("regions")
    if type(regions) is not dict:
        raise ValueError("envelope regions must be an object")
    manifest = {name: math.prod(shape) for name, shape in capture_manifest()}
    for case_id in sorted(regions):
        if case_id not in sentinel_cases:
            raise ValueError("envelope names a non-sentinel case: " + case_id)
        for seam, declaration in sorted(regions[case_id].items()):
            label = case_id + " " + seam
            if seam not in manifest:
                raise ValueError("envelope names an unknown seam: " + label)
            if not isinstance(declaration, dict) or set(declaration) != {
                "count",
                "guarantee",
                "max_abs_difference",
                "reference",
                "reference_sha256",
            }:
                raise ValueError("envelope declaration fields malformed: " + label)
            if declaration["guarantee"] != "declared-bounded-deviation":
                raise ValueError("unknown envelope guarantee: " + label)
            bound = declaration["max_abs_difference"]
            if (
                type(bound) is not float
                or not math.isfinite(bound)
                or not 0.0 < bound < 1.0
            ):
                raise ValueError("envelope bound not a finite float in (0, 1): " + label)
            if declaration["reference"] != case_id + "." + seam + ".f32le":
                raise ValueError("envelope reference name mismatch: " + label)
            digest = declaration["reference_sha256"]
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("envelope reference digest malformed: " + label)
            if declaration["count"] != manifest[seam]:
                raise ValueError("envelope reference count mismatch: " + label)
    return data


def read_envelope_reference(references_root, case_id, seam, declaration):
    path = Path(references_root) / declaration["reference"]
    data = path.read_bytes()
    if sha256(data) != declaration["reference_sha256"]:
        raise ValueError("envelope reference digest mismatch: " + path.name)
    if len(data) != declaration["count"] * 4:
        raise ValueError("envelope reference count mismatch: " + path.name)
    return data


def envelope_projection(record, region):
    """Declared-region case projection.

    Identity fields stay byte-exact; declared seams assert name, shape and
    the declared guarantee (per-sample deviation is bounded against the
    committed reference bytes by the caller); the audio-hash field of a
    declared case is identity of the variable audio and is excluded; the
    normalization peak keeps its threshold identity when its source trace
    (mixer.peak) is declared, since the field is the unpacked identity of
    that trace.
    """
    traces = []
    for seam_record in record["traces"]:
        name = seam_record["name"]
        if name in region:
            traces.append(
                {
                    "name": name,
                    "shape": seam_record["shape"],
                    "declared_guarantee": region[name]["guarantee"],
                }
            )
        else:
            traces.append(dict(seam_record))
    normalization = dict(record["normalization"])
    if "mixer.peak" in region:
        normalization["peak"] = 1.0 if normalization["peak"] > 1.0 else 0.0
    return {
        "id": record["id"],
        "case_definition": record["case_definition"],
        "corpus_coordinates": record["corpus_coordinates"],
        "is_unmodified_corpus_sound": record["is_unmodified_corpus_sound"],
        "execution_width": record["execution_width"],
        "reproducible": record["reproducible"],
        "configuration": record["configuration"],
        "normalized_by_name": record["normalized_by_name"],
        "parameter_order": record["parameter_order"],
        "selected_noise_sha256": record["selected_noise_sha256"],
        "traces": traces,
        "passive_capture_invariant": True,
        "no_hook_audio_sha256": None,
        "normalization": normalization,
    }


def verify_expected(
    observed,
    expected,
    case_ids,
    envelope=None,
    references_root=None,
    runs_root=None,
):
    """An actual-render sentinel must retain the committed selected seam bytes.

    Cases named in the declared envelope (DR-0009 amendment A1) assert their
    declared guarantees instead of byte equality; every other case and every
    undeclared seam keeps the byte-exact assertion.
    """
    for report in (observed, expected):
        if report["status"] != "PASS" or not report["fresh_process_repeats_equal"]:
            raise ValueError("sentinel requires completed repeat evidence")
    for key in (
        "name",
        "python",
        "machine",
        "packages",
        "torch_build",
        "device",
        "dtype",
        "threads",
        "interop_threads",
        "math_environment",
    ):
        require_provenance(expected["runtime"][key], observed["runtime"][key])
    require_provenance(expected["provenance"], observed["provenance"])
    regions = (envelope or {}).get("regions", {})
    for case_id in case_ids:
        selected = []
        for report in (expected, observed):
            matching = [c for c in report["comparisons"] if c["id"] == case_id]
            if len(matching) != 1:
                raise ValueError("missing or duplicate sentinel case: " + case_id)
            selected.append(matching[0])
        region = regions.get(case_id)
        for side in ("canonical", "scalar"):
            a, b = [c[side] for c in selected]
            require_trace(a["traces"])
            require_trace(b["traces"])
            require_named(a["normalized_by_name"], b["normalized_by_name"])
            if region is None:
                if a != b:
                    raise ValueError("sentinel drift: " + case_id + " " + side)
                continue
            if envelope_projection(a, region) != envelope_projection(b, region):
                raise ValueError("sentinel drift: " + case_id + " " + side)
        if not region:
            continue
        if runs_root is None:
            raise ValueError("declared-envelope case requires the run root: " + case_id)
        for seam, declaration in sorted(region.items()):
            reference = read_envelope_reference(
                references_root, case_id, seam, declaration
            )
            reference_samples = [v[0] for v in struct.iter_unpack("<f", reference)]
            bound = declaration["max_abs_difference"]
            for side in ("canonical", "scalar"):
                path = Path(runs_root) / "run-1" / side / (
                    case_id + "." + seam + ".f32le"
                )
                samples = [v[0] for v in struct.iter_unpack("<f", path.read_bytes())]
                if len(samples) != declaration["count"]:
                    raise ValueError(
                        "sentinel reference count mismatch: " + case_id + " " + seam
                    )
                if not all(math.isfinite(v) for v in samples):
                    raise ValueError("nonfinite sample: " + case_id + " " + seam)
                if any(abs(s - r) > bound for s, r in zip(samples, reference_samples)):
                    raise ValueError(
                        "sentinel drift beyond declared envelope bound: "
                        + case_id
                        + " "
                        + seam
                    )
            print(
                "envelope verified: "
                + case_id
                + " "
                + seam
                + " (declared bound "
                + repr(bound)
                + ")"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("render", "aggregate", "preflight", "verify")
    )
    parser.add_argument("--side", choices=("canonical", "scalar"))
    parser.add_argument("--source-root", type=Path, default=Path("/opt/torchsynth"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--canonical", type=Path)
    parser.add_argument("--campaign-id")
    parser.add_argument("--repeat", type=int, choices=(1, 2))
    parser.add_argument(
        "--expected", type=Path, default=REPO / "sim/reference/scalar-execution.json"
    )
    parser.add_argument("--envelope", type=Path, default=ENVELOPE_PATH)
    parser.add_argument("--references", type=Path, default=ENVELOPE_REFERENCES)
    parser.add_argument("--sentinel", action="store_true")
    parser.add_argument(
        "--mutation", choices=("wrong-parameter", "wrong-noise", "fresh-randomization")
    )
    args = parser.parse_args()
    execution = {
        "uuid": str(uuid.uuid4()),
        "pid": os.getpid(),
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),  # noqa: UP017 -- Python 3.9 runtime
        "campaign_id": args.campaign_id,
        "repeat": args.repeat,
    }
    if args.action == "verify":
        plan, _ = load_plan(True)
        observed = json.loads((args.output / "scalar-execution.json").read_text())
        expected = json.loads(args.expected.read_text())
        if observed.get("scope") not in ("sentinel", "full-preregistered-cases"):
            raise ValueError("unknown aggregate scope")
        replayed = aggregate(args.output, observed["scope"] == "sentinel", write=False)
        for key in (
            "scope",
            "status",
            "byte_equivalence",
            "fresh_render_processes",
            "fresh_process_repeats_equal",
            "provenance",
            "runtime",
            "execution_records",
            "campaign_id",
            "comparisons",
            "negative_controls",
            "run_report_sha256",
        ):
            require_provenance(replayed[key], observed[key])
        verify_expected(
            observed,
            expected,
            plan["sentinel"],
            envelope=load_envelope(args.envelope),
            references_root=args.references,
            runs_root=args.output,
        )
        print("Committed sentinel seam bytes reproduced")
        return 0
    if args.action == "aggregate":
        aggregate(args.output, args.sentinel)
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    stdout, stderr = io.StringIO(), io.StringIO()
    with (
        warnings.catch_warnings(record=True) as caught,
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        warnings.simplefilter("always")
        try:
            if args.action == "preflight":
                plan, _ = load_plan(args.sentinel)
                report = {
                    "status": "PASS",
                    "provenance": provenance(args.source_root, plan),
                }
            else:
                if (
                    not args.side
                    or (args.side == "scalar" and not args.canonical)
                    or (args.mutation and args.side != "scalar")
                ):
                    raise ValueError(
                        "render requires side, canonical inputs for scalar, and scalar-only mutations"
                    )
                require_execution(
                    {"side": args.side, "execution": execution}, args.side
                )
                report = run_render(args)
        except Exception as error:  # noqa: BLE001 -- persist failure and traceback, then exit nonzero
            report = {
                "status": "NO_VERDICT",
                "error": type(error).__name__ + ": " + str(error),
                "traceback": traceback.format_exc(),
            }
            if hasattr(args, "control_observation"):
                report["control_observation"] = args.control_observation
    report.update(
        side=args.side,
        execution=execution,
        mutation=args.mutation,
        torchsynth_imported="torchsynth" in sys.modules,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
        warnings=[
            {
                "category": w.category.__name__,
                "message": str(w.message),
                "file": Path(w.filename).name,
                "line": w.lineno,
            }
            for w in caught
        ],
    )
    if hasattr(args, "canonical_execution_uuid"):
        report["canonical_execution_uuid"] = args.canonical_execution_uuid
        report["canonical_report_sha256"] = args.canonical_report_sha256
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(report["status"] + (": " + report["error"] if "error" in report else ""))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
