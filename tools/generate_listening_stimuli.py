#!/usr/bin/env python3
"""Deterministic listening-stimulus generator for the issue 44 protocol scaffold.

Agent-doable scaffolding declared in the issue 44 protocol-draft comment
(item 1: stimulus generator; item 3: selection/stratification). Everything
perceptual is parameterized by a ratified (or explicitly allow-listed
unratified) protocol config document
(``spec/reference/listening-protocol-config-v1.schema.json``); this tool
hardcodes no protocol design.

What it does, fail-closed at every step:

- Loads and validates the protocol config (matrix-grounded: every ladder
  point must sit inside the declared magnitude range of the landed
  ``sim/reference/mutation-matrix-v1.json``; operators outside listening
  scope are refused).
- Refuses unratified configs unless ``--allow-unratified`` is passed; any
  output produced without ratification is stamped
  ``not_protocol_evidence`` in-band and in its ``status`` field.
- Selects development-corpus cases only (holdout refused structurally),
  applying the config's preregistered exclusions from the coverage receipt
  and drawing deterministically with the config's recorded seed.
- Renders ``case x operator x ladder-step`` stimuli in memory by composing
  the landed signal-family applications (``torchsynth_voice.mutations_signal``)
  at the config-specified magnitudes. Operators declared ``host-run-gated``
  (parameter/producer/control-lane seams: the DR-0006 gated actual-Voice
  bridge) are recorded as skipped rows, never silently rendered.
- For each stimulus: records the verbatim refusal of the honest ``mp1-``
  plan-binding attempt (corpus audio is a mix output, not a declared
  post-module trace), a domain-separated ``ls1i-`` stimulus identity, the
  SHA-256 of the stimulus and its reference audio (float32 little-endian),
  and a ``compare_paired`` measurement summary (``pm1-`` diagnostic
  identity, per spec/PAIRED-METRICS.md) of degraded vs reference. Paired
  rows stay unaligned and un-gain-fit; level-condition RMS matching is a
  playback-only derivation recorded alongside, never a measurement input.
- Custody: writes receipts (JSON) only. ``--audio-dir`` may materialize
  stimulus audio for a post-ratification session, but only OUTSIDE the
  repository; the default writes no audio at all.

Exit codes: 0 success, 2 refusal (config/custody/scope), 1 unexpected error.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from listening_protocol_common import (  # noqa: E402
    CustodyRefusal,
    DEV_POOL,
    MATRIX_PATH,
    RENDER_CORPUS_AUDIO,
    ROOT,
    STIMULUS_MANIFEST_SCHEMA,
    UPSTREAM_PATH,
    ProtocolConfigError,
    canonical_bytes,
    config_identity,
    corpus_audio_applications,
    coverage_facts,
    declared_operators,
    development_cases,
    ethics_complete,
    f32le_decode,
    f32le_encode,
    implementation_sha256,
    load_config,
    matrix_operator_specs,
    mp1_plan_binding_attempt,
    rms,
    select_cases,
    sha256_hex,
    stimulus_code,
    ls1_stimulus_identity,
)

SAMPLE_RATE_HZ = 44100
CLIP_BOUNDS = (-1.0, 1.0)  # corpus audio native unit, full scale = 1.0
PAIRED_UNIT = "amplitude"


class GenerationRefusal(RuntimeError):
    pass


def _read_store_bytes(
    store: Path, relative: str, expected_sha256: str, label: str
) -> bytes:
    path = store / relative
    if not path.is_file():
        raise GenerationRefusal(
            "%s: store file missing (refusing to create): %s" % (label, path)
        )
    data = path.read_bytes()
    observed = sha256_hex(data)
    if observed != expected_sha256:
        raise GenerationRefusal(
            "%s: store file sha256 mismatch for %s (expected %s, observed %s)"
            % (label, relative, expected_sha256, observed)
        )
    return data


def _case_audio(store: Path, case: Dict[str, Any]) -> Tuple[str, List[float]]:
    ref = case["artifact"]
    locator = "artifacts/%s/metadata.json" % ref["artifact_id"]
    if ref["ref"] != locator:
        raise GenerationRefusal(
            "case %s: artifact locator mismatch: %s" % (case["case_id"], ref["ref"])
        )
    metadata_bytes = _read_store_bytes(
        store, ref["ref"], ref["sha256"], "case %s" % case["case_id"]
    )
    record = json.loads(metadata_bytes.decode("utf-8"))
    file_ref = record["audio"]["value"]["file"]
    audio_relative = "artifacts/%s/%s" % (ref["artifact_id"], file_ref["ref"])
    audio_bytes = _read_store_bytes(
        store, audio_relative, file_ref["sha256"], "case %s" % case["case_id"]
    )
    if len(audio_bytes) != file_ref["size_bytes"]:
        raise GenerationRefusal("case %s: audio byte count mismatch" % case["case_id"])
    samples = f32le_decode(audio_bytes)
    if not all(math.isfinite(value) for value in samples):
        raise GenerationRefusal(
            "case %s: decoded audio contains non-finite samples; the coverage receipt's "
            "finite-sample discipline requires finite renders" % case["case_id"]
        )
    return file_ref["sha256"], samples


def _paired_summary(measurement: Dict[str, Any]) -> Dict[str, Any]:
    metrics = measurement["metrics"]
    summary: Dict[str, Any] = {}
    for name in (
        "framing_match",
        "exact_equal",
        "mismatch_count",
        "mean_error",
        "mean_abs_error",
        "error_rms",
        "max_abs_error",
        "snr_db",
    ):
        entry = metrics.get(name) or {}
        summary[name] = entry.get("value")
        if entry.get("reason") is not None and entry.get("value") is None:
            summary[name + "_refusal"] = entry.get("reason")
    return summary


def _paired_identity(
    measurement: Dict[str, Any], stimulus_code_value: str, case_id: str
) -> Dict[str, Any]:
    from torchsynth_voice.paired_metrics import scorecard_rows

    rows, diagnostic_bytes = scorecard_rows(
        measurement,
        case_id=stimulus_code_value,
        partition="development",
        trace="corpus-audio",
        rubric=None,
    )
    return {
        "diagnostic_identity": rows[0]["artifact"]["identity"],
        "diagnostic_sha256": sha256_hex(diagnostic_bytes),
        "case_id_reference": case_id,
        "note": "rows carry NO VERDICT (no rubric); no verdict is claimed by this scaffold",
    }


def _playback_variant(
    reference: List[float],
    degraded: List[float],
    level_condition: str,
) -> Dict[str, Any]:
    """Playback-only derivation for the declared level condition.

    C1 rms-matched: gain the degraded clip to the reference RMS for playback.
    C2 native: unchanged. This is presentation harnessing only and never
    feeds the paired rows, which stay unaligned and un-gain-fit.
    """
    if level_condition == "C2":
        return {
            "level_condition": "C2",
            "playback_gain_linear": 1.0,
            "playback_variant_of": "degraded",
        }
    deg_rms = rms(degraded)
    ref_rms = rms(reference)
    if deg_rms == 0.0:
        return {
            "level_condition": "C1",
            "playback_gain_linear": None,
            "refusal": "degraded_silent_rms_undefined",
        }
    gain = ref_rms / deg_rms
    return {
        "level_condition": "C1",
        "playback_gain_linear": gain,
        "playback_rms_target": ref_rms,
        "note": "playback-only; spec/PAIRED-METRICS.md rows remain unaligned/un-gain-fit",
    }


def _level_for(cfg: Dict[str, Any], operator: str) -> str:
    assignment = cfg["level_conditions"]["assignment"]
    return assignment["operators"].get(operator, assignment["default"])


def _check_custody_path(path: Path, flag: str) -> Path:
    resolved = Path(path).resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise CustodyRefusal(
            "%s resolves inside the repository (%s); custody is receipts-only and stimulus "
            "material must never be committed. Choose a directory outside %s."
            % (flag, resolved, ROOT)
        )
    return resolved


def generate(
    *,
    config_path: Path,
    receipt_path: Path,
    coverage_path: Path,
    store: Path,
    out_path: Path,
    audio_dir: Optional[Path],
    diagnostics_dir: Optional[Path],
    allow_unratified: bool,
    seed_override: Optional[int],
) -> Dict[str, Any]:
    matrix_bytes = MATRIX_PATH.read_bytes()
    matrix = json.loads(matrix_bytes.decode("utf-8"))
    cfg = load_config(config_path, matrix=matrix)
    cfg_id_hash = config_identity(cfg)
    if seed_override is not None:
        cfg = json.loads(json.dumps(cfg))
        cfg["selection"]["seed"] = int(seed_override)
        # Re-derive the identity so receipts bind the executed selection seed.
        cfg_id_hash = config_identity(cfg)

    ratified = bool(cfg["ratified"])
    if not ratified and not allow_unratified:
        raise GenerationRefusal(
            "protocol config %r is not ratified (ratified=false): generation is inert until the "
            "operator ratifies. Pass --allow-unratified only for validation/determinism runs; "
            "outputs are then stamped not_protocol_evidence." % cfg["config_id"]
        )

    declared = declared_operators(cfg)
    supported = corpus_audio_applications()
    specs = matrix_operator_specs(matrix)
    for op in sorted(declared):
        entry = declared[op]
        if entry["render"] == RENDER_CORPUS_AUDIO and op not in supported:
            raise GenerationRefusal(
                "operator %r is declared corpus-audio but the landed implementations expose no "
                "sample-domain application for it; declare it host-run-gated or extend the landed "
                "operators first (this scaffold never re-derives mutation semantics)."
                % op
            )

    receipt = json.loads(receipt_path.read_bytes().decode("utf-8"))
    coverage = json.loads(coverage_path.read_bytes().decode("utf-8"))
    upstream = json.loads(UPSTREAM_PATH.read_bytes().decode("utf-8"))
    source_commit = receipt.get("source_commit")
    if source_commit != upstream.get("target_commit"):
        raise GenerationRefusal(
            "corpus receipt source_commit %r does not match the pinned upstream %r"
            % (source_commit, upstream.get("target_commit"))
        )
    index_ref = receipt.get("run", {}).get("index_ref")
    index_sha = receipt.get("run", {}).get("index_sha256")
    if index_ref and index_sha:
        index_bytes = _read_store_bytes(store, index_ref, index_sha, "corpus index")
        if json.loads(index_bytes.decode("utf-8")) != receipt["index"]:
            raise GenerationRefusal(
                "store index document differs from the receipt's embedded index"
            )

    cases = development_cases(receipt)
    facts = coverage_facts(coverage)
    selection = select_cases(cfg, cases, facts, declared)

    from torchsynth_voice.paired_metrics import compare_paired

    references: Dict[str, Dict[str, Any]] = {}
    stimuli: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    written_audio: List[str] = []

    def write_audio(name: str, samples: List[float]) -> Optional[str]:
        if audio_dir is None:
            return None
        target = audio_dir / name
        target.write_bytes(f32le_encode(samples))
        written_audio.append(name)
        return target.name

    def reference_receipt(
        case: Dict[str, Any], audio_sha: str, samples: List[float]
    ) -> Dict[str, Any]:
        case_id = case["case_id"]
        code = stimulus_code(cfg_id_hash, case_id, "", None)
        return {
            "stimulus_code": code,
            "role": "reference",
            "case_id": case_id,
            "split": case["split"],
            "operator": None,
            "ladder_step": None,
            "magnitude": None,
            "unit": None,
            "ls1_stimulus_identity": ls1_stimulus_identity(
                cfg_id_hash,
                sha256_hex(matrix_bytes),
                implementation_sha256(),
                case_id,
                "",
                None,
                None,
            ),
            "binding": mp1_plan_binding_attempt(case_id, "gain.db", 0.0),
            "binding_note": "reference audio is the unmutated corpus render; mp1- attempt shown for the seam refusal record",
            "corpus": {
                "audio_sha256": audio_sha,
                "artifact_id": case["artifact"]["artifact_id"],
            },
            "audio": {
                "sample_count": len(samples),
                "rate_hz": float(SAMPLE_RATE_HZ),
                "dtype": "float32le",
                "sha256": audio_sha,
            },
            "file_written": write_audio("ref-%s.f32" % case_id, samples),
        }

    for op in sorted(declared):
        entry = declared[op]
        if entry["render"] != RENDER_CORPUS_AUDIO:
            skipped.append(
                {
                    "operator": op,
                    "kind": entry["kind"],
                    "render": entry["render"],
                    "reason": (
                        "host-run-gated: requires the DR-0006 gated actual-Voice bridge "
                        "(matrix not_run); no stimulus rendered by this tool"
                    ),
                    "ladder_points": entry["points"],
                    "unit": entry["unit"],
                }
            )
            continue
        apply_operator = supported[op]
        matrix_spec = specs.get(op)
        if matrix_spec is None:
            raise GenerationRefusal("operator %r missing from the mutation matrix" % op)
        draw = selection[op]
        for case_id in draw["cases"]:
            case = next(c for c in cases if c["case_id"] == case_id)
            audio_sha, reference = _case_audio(store, case)
            if case_id not in references:
                references[case_id] = reference_receipt(case, audio_sha, reference)
            level_condition = _level_for(cfg, op)
            if entry["kind"] == "ladder":
                conditions = [
                    (step + 1, entry["points"][step])
                    for step in range(len(entry["points"]))
                ]
            else:
                conditions = [(None, None)]
            for step, magnitude in conditions:
                code = stimulus_code(cfg_id_hash, case_id, op, step)
                identity = ls1_stimulus_identity(
                    cfg_id_hash,
                    sha256_hex(matrix_bytes),
                    implementation_sha256(),
                    case_id,
                    op,
                    step,
                    magnitude,
                )
                degraded, detail = apply_operator(reference, magnitude)
                degraded_sha = sha256_hex(f32le_encode(degraded))
                measurement = compare_paired(
                    reference,
                    degraded,
                    reference_rate_hz=float(SAMPLE_RATE_HZ),
                    candidate_rate_hz=float(SAMPLE_RATE_HZ),
                    unit=PAIRED_UNIT,
                    clip_bounds=CLIP_BOUNDS,
                )
                paired = _paired_summary(measurement)
                paired["declared_clip_bounds"] = list(CLIP_BOUNDS)
                paired["unit"] = PAIRED_UNIT
                paired_identity = _paired_identity(measurement, code, case_id)
                playback = _playback_variant(reference, degraded, level_condition)
                if diagnostics_dir is not None:
                    from torchsynth_voice.paired_metrics import scorecard_rows

                    _, diagnostic_bytes = scorecard_rows(
                        measurement,
                        case_id=code,
                        partition="development",
                        trace="corpus-audio",
                        rubric=None,
                    )
                    target = diagnostics_dir / ("%s.pm1.json" % code)
                    target.write_bytes(diagnostic_bytes)
                    written_audio.append(target.name)
                receipt_row = {
                    "stimulus_code": code,
                    "role": "degraded",
                    "case_id": case_id,
                    "split": case["split"],
                    "operator": op,
                    "family": matrix_spec["family"],
                    "ladder_step": step,
                    "magnitude": magnitude,
                    "unit": entry["unit"],
                    "listen_only_evidence": case_id in draw["listen_only"],
                    "ls1_stimulus_identity": identity,
                    "binding": mp1_plan_binding_attempt(case_id, op, magnitude),
                    "operator_detail": detail,
                    "corpus": {
                        "audio_sha256": audio_sha,
                        "artifact_id": case["artifact"]["artifact_id"],
                    },
                    "audio": {
                        "sample_count": len(degraded),
                        "rate_hz": float(SAMPLE_RATE_HZ),
                        "dtype": "float32le",
                        "sha256": degraded_sha,
                    },
                    "playback": playback,
                    "paired_metrics": paired,
                    "paired_diagnostic": paired_identity,
                }
                stimuli.append(receipt_row)

    stimuli.sort(
        key=lambda row: (
            row["operator"] or "",
            row["case_id"],
            row["ladder_step"] is None,
            row["ladder_step"] or 0,
        )
    )

    custody = {
        "receipts_only": True,
        "audio_written": bool(written_audio),
        "audio_dir": str(audio_dir) if audio_dir is not None else None,
        "audio_files": written_audio,
        "policy": "stimulus audio is never committed; receipts carry SHA-256 identities only",
    }
    manifest = {
        "schema": STIMULUS_MANIFEST_SCHEMA,
        "schema_version": 1,
        "status": "RATIFIED-PROTOCOL-STIMULI"
        if ratified
        else "UNRATIFIED-EXAMPLE-NOT-EVIDENCE",
        "evidence_status": "protocol_stimuli"
        if ratified
        else "not_protocol_evidence_unratified_config",
        "protocol_config": {
            "config_id": cfg["config_id"],
            "config_identity_sha256": cfg_id_hash,
            "config_file_sha256": sha256_hex(Path(config_path).read_bytes()),
            "config_path": str(config_path),
            "ratified": ratified,
            "ethics_box_complete": not ethics_complete(cfg),
            "ethics_fields_pending": ethics_complete(cfg),
        },
        "provenance": {
            "matrix_path": str(MATRIX_PATH),
            "matrix_sha256": sha256_hex(matrix_bytes),
            "corpus_receipt_path": str(receipt_path),
            "corpus_receipt_sha256": sha256_hex(receipt_path.read_bytes()),
            "coverage_path": str(coverage_path),
            "coverage_sha256": sha256_hex(coverage_path.read_bytes()),
            "upstream_target_commit": upstream.get("target_commit"),
            "corpus_source_commit": source_commit,
            "store_root": str(store),
            "mutations_signal_sha256": implementation_sha256(),
            "generator": Path(__file__).name,
        },
        "selection": {
            "pool": DEV_POOL,
            "seed": cfg["selection"]["seed"],
            "cases_per_operator": cfg["selection"]["cases_per_operator"],
            "per_operator": selection,
        },
        "skipped": skipped,
        "references": [references[key] for key in sorted(references)],
        "stimuli": stimuli,
        "blinded_index": [
            {
                "stimulus_code": row["stimulus_code"],
                "audio_sha256": row["audio"]["sha256"],
            }
            for row in stimuli
        ]
        + [
            {
                "stimulus_code": references[key]["stimulus_code"],
                "audio_sha256": references[key]["corpus"]["audio_sha256"],
            }
            for key in sorted(references)
        ],
        "custody": custody,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(canonical_bytes(manifest) + b"\n")
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config", required=True, type=Path, help="protocol config JSON"
    )
    parser.add_argument(
        "--receipt", required=True, type=Path, help="development-corpus-first.json"
    )
    parser.add_argument(
        "--coverage", required=True, type=Path, help="development-corpus-coverage.json"
    )
    parser.add_argument(
        "--store", required=True, type=Path, help="corpus store root (read-only)"
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="manifest/receipt output path (JSON)"
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=None,
        help="OPTIONAL local dir for stimulus audio; must be outside the repository",
    )
    parser.add_argument(
        "--diagnostics-dir",
        type=Path,
        default=None,
        help="OPTIONAL local dir for pm1- paired diagnostics; must be outside the repository",
    )
    parser.add_argument(
        "--allow-unratified",
        action="store_true",
        help="validation/determinism escape for ratified=false configs; outputs are stamped not_protocol_evidence",
    )
    parser.add_argument(
        "--seed-override",
        type=int,
        default=None,
        help="reproducibility probe: execute the same config under a different selection seed",
    )
    args = parser.parse_args(argv)
    try:
        audio_dir = (
            _check_custody_path(args.audio_dir, "--audio-dir")
            if args.audio_dir
            else None
        )
        diagnostics_dir = (
            _check_custody_path(args.diagnostics_dir, "--diagnostics-dir")
            if args.diagnostics_dir
            else None
        )
        manifest = generate(
            config_path=args.config,
            receipt_path=args.receipt,
            coverage_path=args.coverage,
            store=args.store,
            out_path=args.out,
            audio_dir=audio_dir,
            diagnostics_dir=diagnostics_dir,
            allow_unratified=args.allow_unratified,
            seed_override=args.seed_override,
        )
    except (ProtocolConfigError, GenerationRefusal, CustodyRefusal) as refusal:
        print("REFUSED: %s" % refusal, file=sys.stderr)
        return 2
    print(
        "%s: %s — %d stimuli, %d references, %d skipped (status: %s)"
        % (
            Path(args.out).name,
            manifest["protocol_config"]["config_id"],
            len(manifest["stimuli"]),
            len(manifest["references"]),
            len(manifest["skipped"]),
            manifest["status"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
