"""Preregistered, name-keyed Voice patches. No Torch import or audio verdicts."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from fractions import Fraction

from .contract import UpstreamContract, repository_root, sha256_file
from .inventory import INVENTORY_PATH, load_json, validate_inventory

ROOT = repository_root()
MANIFEST_PATH = ROOT / "spec/reference/directed-voice-v1.json"
COVERAGE_PATH = ROOT / "spec/reference/directed-coverage-v1.json"
SHAPES = ("sin", "tri", "saw", "rsaw", "sqr")
ENVELOPES = (
    "adsr_1",
    "adsr_2",
    "lfo_1_amp_adsr",
    "lfo_2_amp_adsr",
    "lfo_1_rate_adsr",
    "lfo_2_rate_adsr",
)
DESTINATIONS = ("vco_1_pitch", "vco_1_amp", "vco_2_pitch", "vco_2_amp", "noise_amp")
ENVELOPE_CASES = ("zero-stages", "cut-attack", "cut-decay", "long-release")
PEAKS = {"below": 1 - 2**-24, "tie": 1.0, "above": 1 + 2**-23}
FIXTURE_PROTOCOL = {
    "sample_rate": 44100,
    "control_rate": 441,
    "duration_seconds": 4.0,
    "output_samples": 176400,
    "device": "cpu",
    "dtype": "float32",
    "noise_seed": 13,
    "noise_slot": 0,
    "qualification_batch_size": 32,
    "hardware_execution_width": 1,
    "scalar_batched_equivalence": "unqualified",
}
CONVERSION = {
    "id": "pinned-equations-rational-v1",
    "normalized": "exact finite binary32 in [0,1]; authoritative patch input",
    "physical": "exact rational evaluation of pinned mapping equations, rounded once to binary64; reciprocal curves are integers in this inventory",
    "bounds": "inventory bounds, including float32-derived pi",
    "check": "exact recomputation; no audio or runtime-conversion tolerance",
    "runtime_obligation": "Record actual float32 physical values when applying normalized inputs; analytic physical values are not a Torch bit-exact oracle.",
}


def encode(document: dict) -> bytes:
    return (
        json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def inventory_rows() -> dict:
    document = load_json(INVENTORY_PATH)
    validate_inventory(document)
    return {row["name"]: row for row in document["parameters"]}


def _f32(value: float) -> float:
    return struct.unpack("!f", struct.pack("!f", value))[0]


def physical_value(row: dict, normalized: float) -> float:
    """Analytic source equations, with exact polynomial evaluation.

    All default reciprocal curves are integers, so this removes host libm
    variation without claiming to reproduce float32 log2/exp2 rounding. The curve=1
    symmetric branch deliberately follows the pinned source (not its docstring);
    no current default Voice range takes that branch.
    """
    value = Fraction(normalized)
    minimum, maximum = Fraction(row["minimum"]), Fraction(row["maximum"])
    exponent = 1.0 / row["curve"]
    if not exponent.is_integer():
        raise ValueError("unsupported noninteger reciprocal curve")
    exponent = int(exponent)
    if row["symmetric"]:
        dist = 2 * value - 1
        if row["curve"] != 1:
            value = abs(dist) ** exponent * (1 if dist > 0 else -1)
        return float(minimum + (maximum - minimum) / 2 * (value + 1))
    if row["curve"] != 1:
        value **= exponent
    return float(minimum + (maximum - minimum) * value)


def _normalized(row: dict, physical: float) -> float:
    value = (physical - row["minimum"]) / (row["maximum"] - row["minimum"])
    if row["symmetric"]:
        dist = 2 * value - 1
        value = (1 + math.copysign(abs(dist) ** row["curve"], dist)) / 2
    else:
        value = value ** row["curve"]
    return _f32(value)


def _pairs(normalized: dict, rows: dict) -> dict:
    return {
        name: {"normalized": value, "physical": physical_value(rows[name], value)}
        for name, value in normalized.items()
    }


def boundary_points(row: dict) -> dict:
    # noise's x**40 mapping would underflow float32 at 2**-12. Use 2**-3
    # instead (physical 2**-120). Symmetric centers use wider neighborhoods
    # so the fifth-power VCO mapping does not collapse under cancellation.
    low = 2**-3 if row["name"] == "mixer.noise" else 2**-12
    points = {"lower": 0.0, "near-lower": low, "near-upper": 1 - 2**-12, "upper": 1.0}
    if row["symmetric"]:
        points.update(
            {"center-below": 0.5 - 2**-5, "center": 0.5, "center-above": 0.5 + 2**-5}
        )
    return points


def _base(rows: dict) -> dict:
    physical = {name: 0.0 for name in rows}
    physical.update(
        {
            "keyboard.midi_f0": 69.0,
            "keyboard.duration": 1.5,
            "mixer.vco_1": 0.25,
            "mod_matrix.adsr_1->vco_1_amp": 1.0,
        }
    )
    for env in ENVELOPES:
        for parameter, value in {
            "attack": 0.02,
            "decay": 0.1,
            "sustain": 0.75,
            "release": 0.2,
            "alpha": 1.0,
        }.items():
            physical[f"{env}.{parameter}"] = value
    for lfo in ("lfo_1", "lfo_2"):
        physical.update(
            {
                f"{lfo}.frequency": 2.0,
                f"{lfo}.mod_depth": 10.0,
                f"{lfo}.sin": 1.0,
                f"{lfo}.tri": 0.5,
            }
        )
    return {name: _normalized(rows[name], value) for name, value in physical.items()}


def _route_context(base: dict, rows: dict, route: str) -> dict:
    patch = base.copy()
    for name in patch:
        if name.startswith(("mod_matrix.", "mixer.")):
            patch[name] = 0.0
    destination = route.split("->")[1]
    carrier = destination.rsplit("_", 1)[0]
    patch[route] = 1.0
    patch[f"mixer.{carrier}"] = _normalized(rows[f"mixer.{carrier}"], 0.25)
    if destination.endswith("_pitch"):
        patch[f"mod_matrix.adsr_1->{carrier}_amp"] = 1.0
        patch[f"{carrier}.mod_depth"] = _normalized(rows[f"{carrier}.mod_depth"], 12.0)
    return patch


def _context(base: dict, rows: dict, target: str) -> dict:
    if target.startswith("mod_matrix."):
        route = target
    elif target.startswith(("lfo_1", "lfo_2")):
        route = f"mod_matrix.{target[:5]}->vco_1_pitch"
    else:
        carrier = "vco_1"
        if target.startswith("mixer."):
            carrier = target.split(".")[1]
        elif target.startswith("vco_2"):
            carrier = "vco_2"
        source = "adsr_2" if target.startswith("adsr_2") else "adsr_1"
        destination = f"{carrier}_amp"
        if target in ("vco_1.mod_depth", "vco_2.mod_depth"):
            source, destination = "adsr_2", f"{carrier}_pitch"
        route = f"mod_matrix.{source}->{destination}"
    return _route_context(base, rows, route)


def _requirements(rows: dict) -> set[tuple[str, str, str]]:
    required = {
        ("boundary", name, point)
        for name, row in rows.items()
        for point in boundary_points(row)
    }
    required.update(
        ("route", name, "isolated") for name in rows if name.startswith("mod_matrix.")
    )
    required.update(
        ("source", source, "isolated") for source in ("vco_1", "vco_2", "noise")
    )
    required.update(
        ("waveform", lfo, shape)
        for lfo in ("lfo_1", "lfo_2")
        for shape in (*SHAPES, "blend")
    )
    required.update(
        ("waveform", "vco_2", shape) for shape in ("square", "blend", "saw")
    )
    required.update(
        ("envelope", env, variant) for env in ENVELOPES for variant in ENVELOPE_CASES
    )
    required.update(
        ("special", variant, variant)
        for variant in ("silence", "near-silence", "stress")
    )
    required.update(("normalization", "mixer", relation) for relation in PEAKS)
    return required


def _case_id(kind: str, target: str, variant: str) -> str:
    if kind in ("source", "route", "special"):
        return f"{kind}:{target}"
    if kind == "normalization":
        return f"{kind}:{variant}"
    return f"{kind}:{target}:{variant}"


def _normalization_target(relation: str) -> dict:
    return {
        "target_peak": PEAKS[relation],
        "relation": relation,
        "peak_status": "unmeasured",
        "render_obligation": "Capture full-clip pre-normalization peak and gain; require exact target peak before claiming this Voice case exercises the branch. Strict peak > 1; tie retains input. No tolerance or gain fitting.",
    }


def _envelope_patch(patch: dict, rows: dict, env: str, variant: str) -> None:
    values = {
        "zero-stages": {"attack": 0, "decay": 0, "release": 0},
        "cut-attack": {"attack": 2, "decay": 1},
        "cut-decay": {"attack": 0.125, "decay": 2},
        "long-release": {"release": 5},
    }[variant]
    for parameter, value in values.items():
        name = f"{env}.{parameter}"
        patch[name] = _normalized(rows[name], value)
    patch["keyboard.duration"] = _normalized(rows["keyboard.duration"], 0.5)


def manifest_identity(document: dict) -> dict:
    payload = {key: value for key, value in document.items() if key != "identity"}
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    digest = hashlib.sha256(data).hexdigest()
    return {"sha256": digest, "version": f"directed-voice-v1-sha256:{digest}"}


def seal(document: dict) -> None:
    """Record a new content version; does not assert coverage or render success."""
    document["identity"] = manifest_identity(document)


def build_manifest() -> dict:
    rows = inventory_rows()
    base = _base(rows)
    cases = []
    for kind, target, variant in sorted(_requirements(rows)):
        context_target = f"mixer.{target}" if kind == "source" else target
        if kind in ("normalization", "special"):
            context_target = "mixer.vco_1"
        patch = _context(base, rows, context_target)
        purpose = f"Preregister {variant} for {target}; inspect its trace and downstream path."
        if kind == "boundary":
            patch[target] = boundary_points(rows[target])[variant]
            purpose = f"Vary only {target} within its routed context: {variant} conversion and trace response (zero may intentionally deactivate it)."
        elif kind == "waveform":
            if target == "vco_2":
                patch["vco_2.shape"] = {"square": 0.0, "blend": 0.5, "saw": 1.0}[
                    variant
                ]
            else:
                for index, shape in enumerate(SHAPES):
                    patch[f"{target}.{shape}"] = (
                        _f32((index + 1) / 8)
                        if variant == "blend"
                        else float(shape == variant)
                    )
        elif kind == "envelope":
            _envelope_patch(patch, rows, target, variant)
        elif kind == "normalization":
            patch["keyboard.duration"] = 1.0
            for env in ("adsr_1", "adsr_2"):
                for parameter in ("attack", "decay", "release"):
                    patch[f"{env}.{parameter}"] = 0.0
                patch[f"{env}.sustain"] = 1.0
            patch["mixer.vco_1"] = PEAKS[variant] / (2 if variant == "above" else 1)
            if variant == "above":
                patch["mod_matrix.adsr_2->vco_1_amp"] = 1.0
            purpose = "Candidate flat-envelope sine peak neighborhood; target is analytic intent, not a measured Voice peak."
        elif kind == "special":
            if variant == "stress":
                for name in patch:
                    if name.startswith(("mod_matrix.", "mixer.")) or name.endswith(
                        (".mod_depth", ".frequency")
                    ):
                        patch[name] = 1.0
            else:
                patch["mixer.vco_1"] = 0.0 if variant == "silence" else 2**-20
        case = {
            "id": _case_id(kind, target, variant),
            "kind": kind,
            "target": target,
            "variant": variant,
            "purpose": purpose,
            "overrides": _pairs(
                {name: value for name, value in patch.items() if value != base[name]},
                rows,
            ),
        }
        if kind == "normalization":
            case["normalization_target"] = _normalization_target(variant)
        cases.append(case)
    document = {
        "schema_version": 1,
        "profile": "torchsynth-1-voice-default",
        "upstream_commit": UpstreamContract.load().target_commit,
        "inventory_sha256": sha256_file(INVENTORY_PATH),
        "fixture_protocol": FIXTURE_PROTOCOL.copy(),
        "conversion": CONVERSION.copy(),
        "base": _pairs(base, rows),
        "cases": cases,
    }
    seal(document)
    validate_manifest(document)
    return document


def _fields(value: object, expected: set, where: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{where}: expected fields {sorted(expected)}")


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _check_map(patch: object, rows: dict, *, complete: bool) -> None:
    if not isinstance(patch, dict):
        raise ValueError("patch must be name-keyed")
    if set(patch) - rows.keys() or (complete and set(patch) != rows.keys()):
        raise ValueError("patch names differ from inventory")
    for name, pair in patch.items():
        _fields(pair, {"normalized", "physical"}, name)
        value, physical = pair["normalized"], pair["physical"]
        if not _number(value) or not 0 <= value <= 1 or _f32(value) != value:
            raise ValueError(f"{name}: normalized must be finite binary32 in [0,1]")
        if not _number(physical) or physical != physical_value(rows[name], value):
            raise ValueError(f"{name}: physical conversion mismatch")


def resolve_patch(document: dict, case: dict) -> dict:
    """Resolve a validated base + overrides into all 78 value pairs by name."""
    return {
        name: pair.copy()
        for name, pair in (document["base"] | case["overrides"]).items()
    }


def _check_context(
    patch: dict, base: dict, rows: dict, target: str, varied: set
) -> None:
    expected = _context(base, rows, target)
    for name in expected:
        if name in varied:
            continue
        if name.startswith("mixer.") and patch[name] != expected[name]:
            raise ValueError(f"carrier isolation: {name}")
        if name.startswith("mod_matrix.") and patch[name] != expected[name]:
            raise ValueError(f"route isolation: {name}")
        if (
            name.endswith(".mod_depth")
            and expected[name] != base[name]
            and patch[name] != expected[name]
        ):
            raise ValueError(f"pitch carrier depth: {name}")
        if patch[name] != expected[name]:
            raise ValueError(f"case supporting context changed: {name}")


def _check_case(case: dict, patch: dict, base: dict, rows: dict) -> None:
    kind, target, variant = case["kind"], case["target"], case["variant"]
    if kind in ("boundary", "route", "source", "waveform", "envelope"):
        context_target = f"mixer.{target}" if kind == "source" else target
        varied = {target} if kind == "boundary" else set()
        if kind == "waveform":
            varied = (
                {"vco_2.shape"}
                if target == "vco_2"
                else {f"{target}.{shape}" for shape in SHAPES}
            )
        if kind == "envelope":
            varied = {
                f"{target}.{parameter}" for parameter in ("attack", "decay", "release")
            }
            varied.add("keyboard.duration")
        _check_context(patch, base, rows, context_target, varied)
    if kind == "boundary" and patch[target] != boundary_points(rows[target])[variant]:
        raise ValueError("boundary case conversion target mismatch")
    if kind == "route" and patch[target] != 1:
        raise ValueError("route isolation requires unit target gain")
    if kind == "waveform":
        if target == "vco_2":
            valid = (
                patch["vco_2.shape"] == {"square": 0, "blend": 0.5, "saw": 1}[variant]
            )
        else:
            weights = {shape: patch[f"{target}.{shape}"] for shape in SHAPES}
            valid = (
                all(value > 0 for value in weights.values())
                and len(set(weights.values())) > 1
                if variant == "blend"
                else all(
                    value == float(shape == variant) for shape, value in weights.items()
                )
            )
        if not valid:
            raise ValueError("waveform regime mismatch")
    if kind == "envelope":
        expected = patch.copy()
        _envelope_patch(expected, rows, target, variant)
        if expected != patch:
            raise ValueError("envelope case timing mismatch")
    if kind == "special":
        if variant != "stress":
            _check_context(patch, base, rows, "mixer.vco_1", {"mixer.vco_1"})
        levels = [patch[f"mixer.{source}"] for source in ("vco_1", "vco_2", "noise")]
        if variant == "silence" and any(levels):
            raise ValueError("silence case mixer gates")
        if variant == "near-silence" and levels != [2**-20, 0, 0]:
            raise ValueError("near-silence case mixer gates")
        if variant == "stress" and any(
            value != 1
            for name, value in patch.items()
            if name.startswith(("mixer.", "mod_matrix."))
            or name.endswith((".frequency", ".mod_depth"))
        ):
            raise ValueError("stress case extremes")
    if kind == "normalization":
        target_claim = case["normalization_target"]
        if (
            not isinstance(target_claim, dict)
            or not _number(target_claim.get("target_peak"))
            or target_claim != _normalization_target(variant)
        ):
            raise ValueError(
                "normalization target must remain unmeasured with exact obligation"
            )
        expected_gain = PEAKS[variant] / (2 if variant == "above" else 1)
        if (
            patch["mixer.vco_1"] != expected_gain
            or patch["mod_matrix.adsr_1->vco_1_amp"] != 1
        ):
            raise ValueError("normalization candidate gain mismatch")
        if patch["mod_matrix.adsr_2->vco_1_amp"] != float(variant == "above"):
            raise ValueError("normalization candidate support route mismatch")
        varied = {"keyboard.duration", "mixer.vco_1", "mod_matrix.adsr_2->vco_1_amp"}
        for env in ("adsr_1", "adsr_2"):
            for parameter in ("attack", "decay", "release", "sustain"):
                name = f"{env}.{parameter}"
                varied.add(name)
                if patch[name] != float(parameter == "sustain"):
                    raise ValueError("normalization candidate envelope mismatch")
        if patch["keyboard.duration"] != 1:
            raise ValueError("normalization candidate duration mismatch")
        _check_context(patch, base, rows, "mixer.vco_1", varied)


def validate_manifest(document: dict) -> None:
    _fields(
        document,
        {
            "schema_version",
            "profile",
            "upstream_commit",
            "inventory_sha256",
            "fixture_protocol",
            "identity",
            "conversion",
            "base",
            "cases",
        },
        "manifest",
    )
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("manifest schema version")
    if document["identity"] != manifest_identity(document):
        raise ValueError("manifest identity mismatch; record a new content version")
    if (
        document["profile"] != "torchsynth-1-voice-default"
        or document["upstream_commit"] != UpstreamContract.load().target_commit
    ):
        raise ValueError("manifest profile/source mismatch")
    if document["inventory_sha256"] != sha256_file(INVENTORY_PATH):
        raise ValueError("manifest inventory mismatch")
    if document["conversion"] != CONVERSION:
        raise ValueError("manifest conversion policy mismatch")
    if encode(document["fixture_protocol"]) != encode(FIXTURE_PROTOCOL):
        raise ValueError("manifest fixture protocol mismatch")
    rows = inventory_rows()
    _check_map(document["base"], rows, complete=True)
    base = {name: pair["normalized"] for name, pair in document["base"].items()}
    if base != _base(rows):
        raise ValueError("base differs from the preregistered supporting context")
    if not isinstance(document["cases"], list):
        raise ValueError("cases must be a list of named cases")
    required, seen, ids = _requirements(rows), set(), set()
    for case in document["cases"]:
        fields = {"id", "kind", "target", "variant", "purpose", "overrides"}
        if isinstance(case, dict) and case.get("kind") == "normalization":
            fields.add("normalization_target")
        _fields(case, fields, "case")
        for name in ("id", "kind", "target", "variant", "purpose"):
            if not isinstance(case[name], str) or not case[name].strip():
                raise ValueError(f"case {name}: nonempty text required")
        key = (case["kind"], case["target"], case["variant"])
        if key not in required or case["id"] != _case_id(*key):
            raise ValueError(f"unsupported case coverage: {case['id']}")
        if case["id"] in ids or key in seen:
            raise ValueError("duplicate case")
        ids.add(case["id"])
        seen.add(key)
        _check_map(case["overrides"], rows, complete=False)
        resolved = resolve_patch(document, case)
        patch = {name: pair["normalized"] for name, pair in resolved.items()}
        for lfo in ("lfo_1", "lfo_2"):
            if not any(
                _f32(patch[f"{lfo}.{shape}"] ** _f32(2.718281828)) > 0
                for shape in SHAPES
            ):
                raise ValueError(f"all-zero {lfo} waveform weights")
        _check_case(case, patch, base, rows)
    if seen != required:
        raise ValueError(f"missing case coverage: {sorted(required - seen)}")


def coverage_report(document: dict) -> dict:
    validate_manifest(document)
    report = {
        "schema_version": 1,
        "manifest_identity": document["identity"].copy(),
        "inventory_sha256": document["inventory_sha256"],
        "case_count": len(document["cases"]),
        "audio_render": "not_run",
        "parameters": {},
        "routes": {},
        "sources": {},
        "waveforms": {},
        "envelopes": {},
        "special": {},
        "normalization": {},
        "traces": {},
        "discrete_modes": {
            "count": 0,
            "reason": "All 78 inventory parameters are continuous.",
        },
    }
    groups = {
        "route": "routes",
        "source": "sources",
        "waveform": "waveforms",
        "envelope": "envelopes",
    }
    for case in document["cases"]:
        kind, target, variant = case["kind"], case["target"], case["variant"]
        if kind == "boundary":
            report["parameters"].setdefault(target, {})[variant] = case["id"]
        elif kind == "normalization":
            report["normalization"][variant] = {
                "case": case["id"],
                **case["normalization_target"],
            }
        elif kind == "special":
            report["special"][variant] = case["id"]
        else:
            report[groups[kind]][f"{target}:{variant}"] = case["id"]

    def trace(name: str, cases: list[str], isolation: str) -> None:
        report["traces"][name] = {
            "cases": cases,
            "status": "planned",
            "isolation": isolation,
        }

    coupled = "Voice evaluates the whole graph. Capture this boundary; upstream dependencies cannot be disabled independently. Other audio sources are gated at the mixer."
    for name in ("midi_f0", "duration"):
        trace(f"keyboard.{name}", [f"boundary:keyboard.{name}:upper"], coupled)
    for env in ENVELOPES:
        trace(
            f"{env}.output",
            [f"envelope:{env}:{variant}" for variant in ENVELOPE_CASES],
            coupled,
        )
    for lfo in ("lfo_1", "lfo_2"):
        for stage in ("raw", "post_control_vca"):
            trace(
                f"{lfo}.{stage}",
                [f"waveform:{lfo}:{shape}" for shape in (*SHAPES, "blend")],
                "Only this LFO feeds the pitch column. Rate/amplitude envelopes and an independent carrier amplitude route are required; capture raw and gated LFO separately.",
            )
    for destination in DESTINATIONS:
        cases = [
            f"route:mod_matrix.{source}->{destination}"
            for source in ("adsr_1", "adsr_2", "lfo_1", "lfo_2")
        ]
        isolation = "One input at a time in this matrix column; other columns zero except carrier amplitude support for pitch cases. Capture matrix/control-upsample boundaries separately."
        trace(f"mod_matrix.{destination}", cases, isolation)
        trace(f"control_upsample.{destination}", cases, isolation)
    for source in ("vco_1", "vco_2", "noise"):
        for stage in ("raw", "post_vca"):
            trace(
                f"{source}.{stage}",
                [f"source:{source}"],
                "Only this audio source has a nonzero mixer gain and amplitude route; raw sources still execute. Noise uses the pinned seed/stream policy at render time.",
            )
    for stage in ("pre_normalization", "peak", "gain", "output"):
        trace(
            f"mixer.{stage}",
            [f"normalization:{relation}" for relation in PEAKS],
            "Serially coupled full-clip reduction and normalization cannot be activated independently. Candidate peaks require exact later measurement; no measured branch coverage yet.",
        )
    return report
