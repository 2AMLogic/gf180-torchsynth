#!/usr/bin/env python3
"""Generate the fixed Voice golden vectors and freeze receipt (issue #54).

Stdlib-only host-side driver. For every declared case this tool renders the
landed float reference (``torchsynth_voice.float_voice``) and the candidate
fixed Voice model (``torchsynth_voice.fixed_voice``) from the *same* resolved
request, compares the two under the DR-0008 Section 10 calibrated bands
(as amended 2026-09-21), and publishes:

- ``sim/reference/fixed-voice-golden-v1.json`` — the freeze receipt: model
  composition, accepted-register bindings (DR-0008 digest, choice register
  digest, constants package digest, quarter-wave table digest), per-case
  golden-vector manifest with per-trace SHA-256 digests, float-vs-fixed
  comparison rows with per-band verdicts, and the double-run byte-identity
  record;
- ``sim/reference/golden-vector-fixed-anchor.json`` — the committed compact
  exact-vector sentinel in the landed harness schema
  (``gf180-torchsynth/golden-vector-v1``): the normalization-stress anchor
  case with its scalar traces plus ``vco_1.raw`` and ``mixer.output``;
- ``sim/reference/fixed-voice-golden-v1-traces/`` — retained raw fixed-model
  traces for the calibration fixtures, as exact ``f32le`` sidecars
  (every accepted audio word is exactly representable: 24-bit words map
  losslessly through division by 2^21) with digests recorded in the receipt.

Custody (the harness schema's convention, per issue #54's AC): vector files
carry inputs, expected words, cycle/sample mapping (canonical clip profile),
and hashes. Only the compact sentinel and the retained sidecars are
committed; the full per-case vectors are regenerable bit-exactly via this
tool and carry per-trace digests in the receipt. Development-corpus physical
maps stay digest-custody (operator-retained upstream presets, the #50
policy): corpus cases contribute digests and comparison rows, never
parameter values.

Selection (declared, bounded): every 28th directed case of
``spec/reference/directed-voice-v1.json`` in file order, plus the #51/#153
calibration fixtures and the normalization family, plus the declared
normalization-stress anchor (``source:vco_1`` with all three mixer levels
scaled by the DR-0006 anchor constant), plus every 12th development-corpus
case (globals 0-95, store-backed). The holdout is never read.

Verdict honesty: M1 rows are judged against the calibrated per-class band
limits; a miss is a FAIL recorded verbatim, never retuned here — composed-model
rows above a band feed the pre-freeze recalibration ledger that DR-0008
Section 10 reserves (thresholds are never increased after freeze). Every
non-M1 row and every genuinely-open band is ``NO VERDICT`` with its reason;
no observation is silently dropped and no open row is reported as a pass.

This tool makes no RTL, synthesis, layout, signoff, hardware-playback, or
sound-fidelity claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_interfaces as fi  # noqa: E402
from torchsynth_voice import float_sources as fs  # noqa: E402
from torchsynth_voice.fixed_voice import (  # noqa: E402
    AcceptedFormats,
    FixedVoiceModel,
    MODEL_IDENTITY,
)
from torchsynth_voice.fixedpoint.choices import ChoiceNotAccepted  # noqa: E402
from torchsynth_voice.float_voice import FloatVoiceModel  # noqa: E402
from torchsynth_voice import golden_vectors as gv  # noqa: E402

DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
RECEIPT_OUT = ROOT / "sim/reference/fixed-voice-golden-v1.json"
SENTINEL_OUT = ROOT / "sim/reference/golden-vector-fixed-anchor.json"
TRACES_OUT = ROOT / "sim/reference/fixed-voice-golden-v1-traces"
CORPUS_STORE = Path("/Users/joseph/dev/gf180-issue19-corpus-store")
SWEEP_RECEIPT = ROOT / "sim/candidates/audio-sources-sweep-v1.json"
CONSTANTS_PACKAGE = ROOT / "tb/sv/gf180_rtl_constants_pkg.sv"
DR_0008 = ROOT / "spec/decision-records/0008-fixed-point-numeric-contract.md"
CHOICES_REGISTER = ROOT / "spec/reference/fixedpoint-choices-v1.json"

RECEIPT_SCHEMA = "gf180-torchsynth/fixed-voice-golden-v1"
RECEIPT_VERSION = 1
SEMVER = "fixed-voice-golden-v1"
ISSUE = 54

DIRECTED_STRIDE = 28
CORPUS_STRIDE = 12
ANCHOR_SCALE = 3.9478583336
CALIBRATION_CASES = (
    "boundary:vco_1.initial_phase:upper",
    "boundary:vco_1.tuning:upper",
    "boundary:vco_1.mod_depth:upper",
    "boundary:vco_2.mod_depth:upper",
    "waveform:vco_2:saw",
    "source:vco_1",
    "source:vco_2",
    "source:noise",
    "normalization:above",
    "normalization:below",
    "normalization:tie",
)
RETAINED_TRACES = {
    "boundary:vco_1.initial_phase:upper": ("vco_1.raw", "vco_2.raw"),
    "boundary:vco_1.tuning:upper": ("vco_1.raw", "vco_2.raw"),
    "boundary:vco_1.mod_depth:upper": ("vco_1.raw", "vco_2.raw"),
    "boundary:vco_2.mod_depth:upper": ("vco_1.raw", "vco_2.raw"),
    "waveform:vco_2:saw": ("vco_2.raw",),
    "normalization-stress:anchor-3.9478583336": ("mixer.output",),
    "normalization:above": ("mixer.output",),
}

M1_LIMITS = {
    "vco_1": ((1000.0, 2.0 ** -9), (5000.0, 2.0 ** -7), (12600.0, 2.0 ** -6)),
    "vco_2": ((1000.0, 2.0 ** -5), (5000.0, None), (12600.0, 2.0 ** -11)),
}
DRIFT_ATTRIBUTION = (
    "float-reference drift attribution (DR-0008 Section 4): the reference "
    "stores binary32 phase partials and f32 pitch words; the calibrated "
    "bands absorb that reference-side drift per oscillator class. The "
    "composed fixed model additionally binds the S1-entry MIDI/frequency "
    "words at their accepted precision (C4/C3), so float-vs-fixed rows "
    "carry the reference's f32 pitch rounding as reference-side error."
)

_SENTINEL_TRACES = (
    "keyboard.midi_f0",
    "keyboard.duration",
    "mixer.peak",
    "mixer.gain",
    "vco_1.raw",
    "mixer.output",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path):
    return json.loads(path.read_bytes())


def m1_limit(vco: str, max_f0: float):
    for bound, limit in M1_LIMITS[vco]:
        if max_f0 <= bound:
            return limit
    return None


def max_fixture_f0(trace, midi_f0, tuning, mod_depth):
    """Maximum instantaneous frequency over the clip (the #153 banding rule)."""

    f32 = fs.f32
    base = f32(f32(midi_f0) + f32(tuning))
    depth = f32(mod_depth)
    exp2 = __import__("math").exp2
    worst = 0.0
    for sample in trace:
        control = f32(base + f32(depth * f32(sample)))
        if control < fs.MIDI_CLAMP_MIN:
            control = fs.MIDI_CLAMP_MIN
        elif control > fs.MIDI_CLAMP_MAX:
            control = fs.MIDI_CLAMP_MAX
        hz = f32(440.0 * f32(exp2(f32(f32(control - fs.MIDI_A440) / fs.SEMITONES_PER_OCTAVE))))
        if hz > worst:
            worst = hz
    return worst


def build_directed_requests(directed):
    base_p = {name: entry["physical"] for name, entry in directed["base"].items()}
    base_n = {name: entry["normalized"] for name, entry in directed["base"].items()}
    by_id = {case["id"]: case for case in directed["cases"]}
    selection = []
    for index in range(0, len(directed["cases"]), DIRECTED_STRIDE):
        selection.append((directed["cases"][index]["id"], "directed-stride-28", {}))
    for case_id in CALIBRATION_CASES:
        if case_id not in by_id:
            raise SystemExit("declared calibration case missing: " + case_id)
        if any(existing[0] == case_id for existing in selection):
            continue
        selection.append((case_id, "directed-calibration", {}))
    selection.append(
        ("normalization-stress:anchor-3.9478583336", "declared-anchor-variant",
         {"mix_scale": ANCHOR_SCALE})
    )
    requests = []
    for case_id, source, variant in selection:
        case = by_id.get(case_id)
        if case is None and source != "declared-anchor-variant":
            raise SystemExit("unknown case: " + case_id)
        physical = dict(base_p)
        normalized = dict(base_n)
        overrides = case["overrides"] if case else {}
        for name, override in overrides.items():
            physical[name] = override["physical"]
            normalized[name] = override["normalized"]
        if variant.get("mix_scale") is not None:
            scale = variant["mix_scale"]
            for lane in ("mixer.vco_1", "mixer.vco_2", "mixer.noise"):
                physical[lane] = float(
                    __import__("fractions").Fraction(str(scale))
                    * __import__("fractions").Fraction(str(physical[lane]))
                )
        noise_bytes = fs.NoiseSource.resolve(0)
        request = fi.ResolvedRequest(
            0,
            normalized,
            {
                "seed": 13,
                "slot": 0,
                "sample_count": 176400,
                "sha256": hashlib.sha256(noise_bytes).hexdigest(),
                "samples": noise_bytes,
            },
            physical=physical,
            execution_status="canonical-batched",
        )
        requests.append(
            {
                "id": case_id,
                "source": source,
                "variant": variant,
                "request": request,
                "parameters_public": True,
            }
        )
    return requests


def build_corpus_requests():
    index_files = sorted((CORPUS_STORE / "indexes").glob("*.json"))
    if not index_files:
        raise SystemExit("development corpus store index not found at " + str(CORPUS_STORE))
    index = load_json(index_files[0])
    dev = [
        entry for entry in index["cases"]
        if entry.get("split") == "development" and entry.get("status") == "complete"
    ]
    dev.sort(key=lambda entry: entry["case_id"])
    requests = []
    for entry in dev[::CORPUS_STRIDE]:
        case_id = entry["case_id"]
        artifact_id = entry["artifact"]["artifact_id"]
        metadata_path = CORPUS_STORE / "artifacts" / artifact_id / "metadata.json"
        metadata = load_json(metadata_path)
        inputs = metadata["inputs"]["value"]
        parameters = inputs["parameters"]
        physical = parameters["physical_by_name"]
        normalized = parameters["normalized_by_name"]
        sound_index = inputs["fixture"]["sound_index"]
        noise_meta = inputs["noise"]
        noise_bytes = fs.NoiseSource.resolve(sound_index)
        noise_sha = hashlib.sha256(noise_bytes).hexdigest()
        if noise_sha != noise_meta["sha256"] or noise_meta["slot"] != sound_index % 32:
            raise SystemExit("corpus noise identity mismatch for " + case_id)
        request = fi.ResolvedRequest(
            sound_index,
            normalized,
            {
                "seed": noise_meta["seed"],
                "slot": noise_meta["slot"],
                "sample_count": 176400,
                "sha256": noise_sha,
                "samples": noise_bytes,
            },
            physical=physical,
            execution_status="canonical-batched",
        )
        requests.append(
            {
                "id": case_id,
                "source": "development-corpus-stride-12",
                "variant": {"store_manifest_sha256": index_files[0].name[:64], "artifact_id": artifact_id},
                "request": request,
                "parameters_public": False,
            }
        )
    return requests


def compare_case(selected, formats):
    request = selected["request"]
    fixed_model = FixedVoiceModel(request, formats)
    float_model = FloatVoiceModel(request)

    fixed, fixed_diag = fixed_model.render()
    float_out, float_diag = float_model.render()

    audio_scale = float(formats.audio.scale)
    midi_f0 = request.physical["keyboard.midi_f0"]
    rows = {}
    for name, float_values in float_out.items():
        fixed_words = fixed[name]
        if name in ("keyboard.midi_f0", "keyboard.duration"):
            rows[name] = {
                "kind": "consumed-identity-scalar",
                "fixed_word": fixed_words[0],
                "float_value": float_values[0],
            }
            continue
        if name in ("mixer.peak", "mixer.gain"):
            fixed_real = fixed_words[0] / (
                audio_scale if name == "mixer.peak" else float(2 ** formats.gain.frac_bits)
            )
            rows[name] = {
                "kind": "derived-scalar",
                "fixed_real": fixed_real,
                "float_value": float_values[0],
                "relative_error": abs(fixed_real - float_values[0]) / max(abs(float_values[0]), 1e-30),
            }
            continue
        fixed_real = [word / audio_scale for word in fixed_words]
        worst = 0.0
        worst_index = 0
        total = 0.0
        squared = 0.0
        first_divergence = None
        mismatches = 0
        for index, (a, b) in enumerate(zip(float_values, fixed_real)):
            delta = a - b
            magnitude = delta if delta >= 0 else -delta
            if magnitude > worst:
                worst = magnitude
                worst_index = index
            total += delta
            squared += delta * delta
            if first_divergence is None and float_values[index] != fixed_real[index]:
                first_divergence = index
            if float_values[index] != fixed_real[index]:
                mismatches += 1
        count = len(float_values)
        row = {
            "kind": "buffer",
            "max_abs_error": worst,
            "max_abs_error_index": worst_index,
            "mean_error": total / count,
            "error_rms": (squared / count) ** 0.5,
            "first_divergence_index": first_divergence,
            "mismatch_count": mismatches,
            "sample_count": count,
        }
        rows[name] = row

    for key in ("vco_1", "vco_2"):
        trace = float_out["control_upsample.%s_pitch" % key]
        max_f0 = max_fixture_f0(
            trace,
            midi_f0,
            request.physical[key + ".tuning"],
            request.physical[key + ".mod_depth"],
        )
        limit = m1_limit(key, max_f0)
        row = rows[key + ".raw"]
        row["m1"] = {
            "vco": key,
            "max_f0_hz": max_f0,
            "band_limit": limit,
            "band_limit_expr": ("2^%d" % __import__("math").log2(limit)) if limit else None,
            "drift_attribution": DRIFT_ATTRIBUTION,
        }
        if limit is None:
            row["verdict"] = "NO VERDICT"
            row["verdict_reason"] = (
                "open or out-of-scope band (DR-0008 Section 10 as amended "
                "2026-09-21): no calibrated limit for this oscillator class "
                "at this fixture f0"
            )
        else:
            row["verdict"] = "PASS" if row["max_abs_error"] <= limit else "FAIL"
            if row["verdict"] == "FAIL":
                row["verdict_reason"] = (
                    "composed-model row above the current calibrated band; "
                    "recorded verbatim for the pre-freeze recalibration "
                    "ledger (DR-0008 Section 10); never retuned in this run"
                )
        row["m1"]["measured_max_abs_error"] = row["max_abs_error"]

    for name, row in rows.items():
        if name in ("vco_1.raw", "vco_2.raw"):
            continue
        if "verdict" not in row:
            row["verdict"] = "NO VERDICT"
            row["verdict_reason"] = (
                "no composed-model band preregistered; recorded for the "
                "pre-freeze calibration ledger (DR-0008 Section 10)"
            )

    branch_agrees = bool(fixed_diag["normalized_branch"]) == bool(float_diag["normalized_branch"])
    manifest = {
        "id": selected["id"],
        "source": selected["source"],
        "variant": selected["variant"],
        "sound_index": request.identity.sound_index,
        "noise": {
            "slot": request.noise["slot"],
            "seed": request.noise["seed"],
            "sha256": request.noise["sha256"],
        },
        "parameters_sha256": sha256_json(request.physical),
        "normalized_sha256": sha256_json(request.normalized),
        "parameters": request.physical if selected["parameters_public"] else None,
        "traces": {name: sha256_json(values) for name, values in fixed.items()},
        "fixed_diagnostics_sha256": sha256_json(fixed_diag),
        "float_diagnostics_sha256": sha256_json(float_diag),
        "fixed_model_identity": fixed_diag["model_identity"],
        "formats_identities": formats.identities,
        "branch": {
            "fixed": bool(fixed_diag["normalized_branch"]),
            "float": bool(float_diag["normalized_branch"]),
            "agrees": branch_agrees,
        },
        "rows": rows,
    }
    retained = RETAINED_TRACES.get(selected["id"], ())
    sidecars = {}
    for trace in retained:
        payload = struct_pack_f32le(fixed[trace])
        file_name = "%s.%s.f32le" % (selected["id"], trace)
        sidecars[trace] = {"file": file_name, "sha256": sha256_bytes(payload)}
        if not TRACES_OUT.exists():
            TRACES_OUT.mkdir(parents=True)
        (TRACES_OUT / file_name).write_bytes(payload)
    if sidecars:
        manifest["retained_traces"] = sidecars
    return manifest


def struct_pack_f32le(words):
    import struct

    audio_scale = float(1 << 21)
    return struct.pack("<%df" % len(words), *[word / audio_scale for word in words])


def build_sentinel(manifest, formats, request):
    fixed = manifest["traces"]
    vector = {
        "schema": gv.VECTOR_SCHEMA,
        "schema_version": gv.SCHEMA_VERSION,
        "provenance": {
            "generator": "tools/generate_fixed_voice_golden.py",
            "model_identity": MODEL_IDENTITY,
            "issue": ISSUE,
            "note": (
                "candidate fixed Voice golden vector: normalization-stress "
                "anchor case (source:vco_1 with mixer levels scaled by the "
                "DR-0006 anchor constant). Values are accepted-format "
                "integer words (synthetic-int); audio words are Q2.21. "
                "Consumed by future RTL as bit-exact expectations; makes "
                "no RTL claim."
            ),
            "dr_0008_status": "Accepted (reviewed merge; 2026-09-21)",
            "dr_0008_constants_package_sha256": file_digest(CONSTANTS_PACKAGE),
            "choices_register_sha256": file_digest(CHOICES_REGISTER),
            "lut_sha256": formats.identities["lut_sha256"],
        },
        "trace_registry_version": gv.load_registry_document()["semantic_version"],
        "parameter_inventory_commit": gv.load_inventory_document()["source"]["commit"],
        "clip": {
            "profile": gv.CANONICAL_PROFILE,
            "sample_rate_hz": gv.CANONICAL_SAMPLE_RATE_HZ,
            "sample_count": gv.CANONICAL_SAMPLE_COUNT,
            "control_rate_hz": gv.CANONICAL_CONTROL_RATE_HZ,
            "control_count": gv.CANONICAL_CONTROL_COUNT,
        },
        "parameters": request.physical,
        "traces": [],
    }
    request_fixed = FixedVoiceModel(request, formats)
    fixed_values, _diag = request_fixed.render()
    for name in _SENTINEL_TRACES:
        registry = gv.load_registry_document()
        meta = next(t for t in registry["traces"] if t["name"] == name)
        vector["traces"].append(
            {
                "name": name,
                "kind": meta["kind"],
                "encoding": "synthetic-int",
                "cycle_start": 0,
                "values": list(fixed_values[name]),
            }
        )
    vector["content_hash"] = gv.compute_content_hash(vector)
    return vector


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=RECEIPT_OUT)
    parser.add_argument("--sentinel", type=Path, default=SENTINEL_OUT)
    args = parser.parse_args()

    try:
        formats = AcceptedFormats()
    except ChoiceNotAccepted as error:
        raise SystemExit("accepted register refused: %s" % error)

    directed = load_json(DIRECTED_PATH)
    cases = build_directed_requests(directed) + build_corpus_requests()

    manifests = []
    for selected in cases:
        manifest = compare_case(selected, formats)
        manifests.append(manifest)
        fails = sum(
            1 for row in manifest["rows"].values() if row.get("verdict") == "FAIL"
        )
        print(
            "%-58s rows=%2d FAIL=%d branch_agrees=%s"
            % (
                manifest["id"],
                len(manifest["rows"]),
                fails,
                manifest["branch"]["agrees"],
            )
        )

    anchor = next(m for m in manifests if m["source"] == "declared-anchor-variant")
    selected = next(c for c in cases if c["id"] == anchor["id"])
    vector = build_sentinel(anchor, formats, selected["request"])
    gv.load_vector(vector)
    args.sentinel.write_text(
        json.dumps(vector, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    sentinel_sha256 = sha256_bytes(args.sentinel.read_bytes())

    register_doc = load_json(CHOICES_REGISTER)
    coverage = {
        "directed_kinds": {},
        "normalization_branches_observed": {"normalized": 0, "unity": 0},
        "checkpoint_traces_per_case": 32,
        "statement": (
            "every case renders all 32 declared checkpoint traces through "
            "both models; the selection spans boundary/envelope/route/"
            "waveform/normalization/source kinds, both oscillator paths "
            "(sine LUT and distortion), the noise lane, the mod matrix and "
            "its upsamples, the control-rate envelope/LFO paths, and the "
            "strict peak>1 normalization branch in both directions"
        ),
    }
    for manifest in manifests:
        kind = manifest["id"].split(":")[0]
        if manifest["source"].startswith("development"):
            kind = "corpus"
        coverage["directed_kinds"][kind] = coverage["directed_kinds"].get(kind, 0) + 1
        if manifest["branch"]["fixed"]:
            coverage["normalization_branches_observed"]["normalized"] += 1
        else:
            coverage["normalization_branches_observed"]["unity"] += 1
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "schema_version": RECEIPT_VERSION,
        "semantic_version": SEMVER,
        "kind": "fixed-voice-golden-release-receipt",
        "issue": ISSUE,
        "status": "CANDIDATE-FROZEN",
        "model": {
            "module": "src/torchsynth_voice/fixed_voice.py",
            "identity": MODEL_IDENTITY,
            "composition": (
                "FixedControlPath(#50 certified baseline) -> mod matrix -> "
                "endpoint-aligned upsample -> fixed sources (u32 wrapping "
                "phase, Q16.15 frequency words, C5 quarter-wave LUT) -> "
                "exact host-fed noise (C8) -> three VCAs -> mixer -> C9 "
                "declared-precision normalization replay"
            ),
            "formats": formats.to_json(),
            "shadow_sites": {
                "policy": (
                    "binary64 shadow on quantized operands; open #74-class "
                    "approximation items, tallied per render in diagnostics"
                ),
                "sites": (
                    "vco.midi_to_hz.exp2.shadow",
                    "vco_2.tanh.shadow",
                    "vco_2.partials_constant.shadow",
                    "adsr alpha and LFO shape weights (format_sweep-declared)",
                ),
            },
            "float_topology_source": "src/torchsynth_voice/float_voice.py",
        },
        "bindings": {
            "dr_0008_status": register_doc["dr_status"],
            "dr_0008_record_sha256": file_digest(DR_0008),
            "choices_register_sha256": file_digest(CHOICES_REGISTER),
            "constants_package": "tb/sv/gf180_rtl_constants_pkg.sv",
            "constants_package_sha256": file_digest(CONSTANTS_PACKAGE),
            "lut_sha256": formats.identities["lut_sha256"],
            "lut_m2_evidence": (
                "sim/candidates/audio-sources-sweep-v1.json lut_tables['4096'] "
                "(M2 measured 2.45576012636306e-07 vs the calibrated 2^-20 "
                "limit; the model's table binds to the same digest)"
            ),
            "upstream_pin": "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d",
        },
        "selection": {
            "directed_stride": DIRECTED_STRIDE,
            "directed_calibration": list(CALIBRATION_CASES),
            "anchor_variant": {
                "id": "normalization-stress:anchor-3.9478583336",
                "declaration": (
                    "source:vco_1 with mixer.vco_1/vco_2/noise scaled by the "
                    "DR-0006 anchor constant 3.9478583336 (level-domain "
                    "declared stress; applied to the physical map before "
                    "both models)"
                ),
            },
            "corpus_stride": CORPUS_STRIDE,
            "corpus_policy": (
                "development globals only, every 12th case of the store "
                "index; holdout never read; physical maps digest-custody "
                "(operator-retained upstream presets, the #50 policy)"
            ),
            "case_count": len(manifests),
        },
        "coverage": coverage,
        "m1_bands": {
            "authority": "DR-0008 Section 10 as amended 2026-09-21 (rubric-v1 R-L3-M1)",
            "vco_1": {"f0<=1kHz": "2^-9", "1kHz<f0<=5kHz": "2^-7", "5kHz<f0<=12.6kHz": "2^-6"},
            "vco_2": {"f0<=1kHz": "2^-5", "1kHz<f0<=5kHz": "open/NO VERDICT", "5kHz<f0<=12.6kHz": "2^-11"},
            "policy": (
                "a miss is a FAIL recorded verbatim; composed-model rows "
                "above a band feed the pre-freeze recalibration ledger; "
                "thresholds are never increased after freeze"
            ),
        },
        "cases": manifests,
        "verdict_tally": tally(manifests),
        "custody": {
            "committed_sentinel": SENTINEL_OUT.name,
            "sentinel_sha256": sentinel_sha256,
            "sentinel_bytes": args.sentinel.stat().st_size,
            "committed_sidecar_dir": str(TRACES_OUT.name),
            "sidecar_encoding": (
                "exact f32le of accepted Q2.21 words (word/2^21 is exact in "
                "binary32); reverse with round(value*2^21)"
            ),
            "full_vectors": (
                "regenerable bit-exactly via this tool; per-trace digests "
                "recorded per case above; no multi-GB payload committed"
            ),
            "corpus_parameters": "digest-custody only, never committed",
        },
        "double_run": {
            "policy": (
                "the generator is deterministic; the release proof is two "
                "clean invocations producing byte-identical receipt, "
                "sentinel, and sidecars (compared externally and recorded "
                "in the PR evidence)"
            ),
        },
        "generation": {
            "commands": [
                "python3 tools/generate_fixed_voice_golden.py",
                "python3 -m compileall -q src tools tests",
                "python3 -m pytest tests/test_fixed_voice.py -q",
            ],
            "environment": (
                "stdlib-only host Python; the generator imports no Torch, "
                "TorchSynth, or NumPy; no network, no PDK, no simulator"
            ),
            "inputs": {
                "directed_manifest": "spec/reference/directed-voice-v1.json",
                "corpus_store_index": "operator-retained store (digest in "
                "per-case variant fields)",
                "accepted_register": "spec/reference/fixedpoint-choices-v1.json",
                "constants_package": "tb/sv/gf180_rtl_constants_pkg.sv",
            },
        },
        "honesty": {
            "rtl_claims": "none; no RTL exists yet (vectors are future-RTL inputs)",
            "open_rows": "recorded NO VERDICT with reasons; never zero-as-pass",
        },
    }
    args.out.write_text(
        json.dumps(receipt, sort_keys=True, indent=1) + "\n", encoding="utf-8"
    )
    print("receipt:", args.out)
    print("sentinel:", args.sentinel)
    return 0


def tally(manifests):
    counts = {"PASS": 0, "FAIL": 0, "NO VERDICT": 0}
    m1_pass = m1_fail = m1_open = 0
    for manifest in manifests:
        for row in manifest["rows"].values():
            verdict = row.get("verdict")
            if verdict in counts:
                counts[verdict] += 1
            if row.get("m1"):
                if verdict == "PASS":
                    m1_pass += 1
                elif verdict == "FAIL":
                    m1_fail += 1
                else:
                    m1_open += 1
    return {
        "all_rows": counts,
        "m1_rows": {"pass": m1_pass, "fail": m1_fail, "no_verdict": m1_open},
    }


if __name__ == "__main__":
    raise SystemExit(main())
