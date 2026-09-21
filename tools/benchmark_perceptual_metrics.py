#!/usr/bin/env python3
"""Benchmark candidate perceptual metrics against landed objective diagnostics.

Issue 45 deliverable. Runs the declared candidate perceptual metrics
(``torchsynth_voice.perceptual_benchmark``) over the ratified issue 44
corruption-ladder pairs, benchmarked against the landed objective paired
diagnostic rows carried by the session 01 manifest receipt.

Custody and honesty rules:

- Stimulus audio stays in the operator's local session directory; the tool
  refuses a session directory inside the repository (receipts-only custody).
- Every benchmarked pair is re-derived from the local reference audio and
  verified byte-exact (SHA-256) against the committed manifest before any
  candidate sees it; any drift refuses the run.
- Candidates whose tool or model dependency is absent produce explicit
  per-pair refusal rows; they are never silently skipped.
- The human-comparison row is emitted verbatim as NO VERDICT ("listening
  not collected — operator declination 2026-09-20"); it is never merged
  into candidate statistics.
- Rows only, no aggregate score; no candidate is promoted by any
  correlation.

Exit codes: 0 success, 2 refusal (custody/binding/config), 1 unexpected error.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from listening_protocol_common import (  # noqa: E402
    CustodyRefusal,
    MATRIX_PATH,
    canonical_bytes,
    config_identity,
    corpus_audio_applications,
    f32le_decode,
    load_config,
    sha256_hex,
)
from torchsynth_voice import perceptual_benchmark as pb  # noqa: E402

DEFAULT_CONFIG = ROOT / "spec" / "reference" / "listening-protocol-config-v1.json"
DEFAULT_MANIFEST = ROOT / "sim" / "reference" / "listening-session-01-manifest.json"
DEFAULT_ESTIMATOR_RECEIPT = ROOT / "sim" / "qualification" / "estimators-v1.json"
DEFAULT_OUT = ROOT / "sim" / "qualification" / "perceptual-benchmark-v1.json"
MULTIRES_ID = pb.MULTIRES_SPECTRAL_PIN["identity"]


class BenchmarkRefusal(RuntimeError):
    pass


def _check_session_dir(path: Path) -> Path:
    resolved = Path(path).resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise CustodyRefusal(
            "--session-dir resolves inside the repository (%s); custody is "
            "receipts-only and session material must stay outside %s" % (resolved, ROOT)
        )
    return resolved


def _load_reference_audio(session_dir: Path, manifest: dict) -> dict:
    audio_dir = session_dir / "audio"
    references = {}
    for row in manifest["references"]:
        case_id = row["case_id"]
        expected = row["audio"]["sha256"]
        name = "ref-%s.f32" % case_id
        path = audio_dir / name
        if not path.is_file():
            raise BenchmarkRefusal(
                "reference audio missing from local session custody: %s" % path
            )
        data = path.read_bytes()
        observed = sha256_hex(data)
        if observed != expected:
            raise BenchmarkRefusal(
                "reference audio sha256 mismatch for %s (manifest %s, observed %s)"
                % (name, expected, observed)
            )
        references[case_id] = f32le_decode(data)
    return references


def _rederive_degraded(applications: dict, row: dict, reference: list) -> list:
    operator = row["operator"]
    apply_operator = applications.get(operator)
    if apply_operator is None:
        raise BenchmarkRefusal(
            "stimulus %s: operator %r has no landed sample-domain application"
            % (row["stimulus_code"], operator)
        )
    degraded, _detail = apply_operator(reference, row["magnitude"])
    return degraded


def _probe_availability() -> dict:
    return {
        MULTIRES_ID: pb.multires_spectral_availability(),
        pb.PEAQ_PIN["identity"]: pb.peaq_availability(),
        pb.VISQOL_PIN["identity"]: pb.visqol_availability(),
        pb.CDPAM_PIN["identity"]: pb.cdpam_availability(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--estimator-receipt", type=Path, default=DEFAULT_ESTIMATOR_RECEIPT
    )
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--bootstrap-trials", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260920)
    args = parser.parse_args(argv)

    session_dir = _check_session_dir(args.session_dir)

    matrix_bytes = MATRIX_PATH.read_bytes()
    matrix = json.loads(matrix_bytes.decode("utf-8"))
    config = load_config(args.config, matrix=matrix)
    if not bool(config["ratified"]):
        raise BenchmarkRefusal(
            "protocol config %r is not ratified; the benchmark runs only over "
            "the ratified corruption ladder" % config["config_id"]
        )
    config_id = config_identity(config)
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    binding = manifest.get("protocol_config") or {}
    if binding.get("config_identity_sha256") != config_id:
        raise BenchmarkRefusal(
            "manifest binds config identity %r, not the ratified %r"
            % (binding.get("config_identity_sha256"), config_id)
        )
    estimator_sha = sha256_hex(args.estimator_receipt.read_bytes())

    applications = corpus_audio_applications()
    references = _load_reference_audio(session_dir, manifest)
    availability = _probe_availability()
    candidate_ids = sorted(availability)

    stimulus_rows = []
    per_case_points: dict = {}
    family_points: dict = {}
    reference_dependence = []
    verified = 0
    executed_multires = 0
    for row in manifest["stimuli"]:
        case_id = row["case_id"]
        reference = references[case_id]
        degraded = _rederive_degraded(applications, row, reference)
        degraded_sha = sha256_hex(struct.pack("<%df" % len(degraded), *degraded))
        if degraded_sha != row["audio"]["sha256"]:
            raise BenchmarkRefusal(
                "stimulus %s: re-derived audio sha256 does not match the "
                "committed manifest row; refusing the run" % row["stimulus_code"]
            )
        verified += 1
        candidates = {}
        metrics_for_points = {}
        metric_value = None
        for identity in candidate_ids:
            probe = availability[identity]
            if not probe["available"]:
                candidates[identity] = {
                    "value": None,
                    "status": "refused",
                    "reason": probe["reason"],
                }
                metrics_for_points[identity] = None
                continue
            if identity != MULTIRES_ID:
                candidates[identity] = {
                    "value": None,
                    "status": "refused",
                    "reason": (
                        "producer_not_wired: an available implementation "
                        "exists but this receipt's producer for %r is landed "
                        "in a later increment" % identity
                    ),
                }
                metrics_for_points[identity] = None
                continue
            outcome = pb.multires_spectral_distance(reference, degraded)
            candidates[identity] = {
                "value": outcome["value"],
                "status": outcome["status"],
                "reason": outcome["reason"],
                "unit": outcome["unit"],
            }
            metric_value = outcome["value"]
            metrics_for_points[identity] = outcome["value"]
        objective = {
            axis: (row.get("paired_metrics") or {}).get(axis)
            for axis in pb.OBJECTIVE_AXES
        }
        stimulus_rows.append(
            {
                "stimulus_code": row["stimulus_code"],
                "case_id": case_id,
                "operator": row["operator"],
                "family": row.get("family"),
                "ladder_step": row.get("ladder_step"),
                "magnitude": row.get("magnitude"),
                "unit": row.get("unit"),
                "objective": objective,
                "candidates": candidates,
            }
        )
        if metric_value is not None:
            executed_multires += 1
            if row.get("ladder_step") is not None:
                per_case_points.setdefault((row["operator"], case_id), []).append(
                    (row["ladder_step"], row["magnitude"], metric_value)
                )
                family_points.setdefault(row.get("family"), []).append(
                    {
                        "operator": row["operator"],
                        "ladder_step": row.get("ladder_step"),
                        "magnitude": row.get("magnitude"),
                        "metrics": metrics_for_points,
                        "objective": objective,
                    }
                )

    multires_probe = availability[MULTIRES_ID]
    for case_id, reference in sorted(references.items()):
        if multires_probe["available"]:
            outcome = pb.multires_spectral_distance(reference, reference)
            reference_dependence.append(
                pb.reference_dependence_row(
                    MULTIRES_ID,
                    outcome["value"],
                    identity_value=outcome["identity_value"],
                )
            )
        else:
            reference_dependence.append(
                {
                    "check": "reference_dependence",
                    "candidate": MULTIRES_ID,
                    "status": "refused",
                    "reason": multires_probe["reason"],
                }
            )

    resampler_rows = []
    for case_id, reference in sorted(references.items()):
        for entry in pb.resampler_contribution_rows(reference):
            resampler_rows.append({"case_id": case_id, **entry})

    agreement = pb.build_agreement(
        family_points,
        candidate_ids,
        availability,
        trials=args.bootstrap_trials,
        seed=args.seed,
    )

    monotonicity = []
    per_case = []
    for (operator, case_id), points in sorted(per_case_points.items()):
        points.sort()
        steps = [step for step, _mag, _v in points]
        values = [v for _step, _mag, v in points]
        if steps == list(range(1, len(points) + 1)):
            per_case.append(
                {
                    "operator": operator,
                    "case_id": case_id,
                    "candidate": MULTIRES_ID,
                    "ladder_spearman_declared_step_vs_metric": pb.spearman_rank(
                        [float(step) for step, _mag, _v in points], values
                    ),
                    "monotonicity": pb.ladder_monotonicity(values),
                }
            )
        by_step: dict = {}
        for step, _mag, value in points:
            by_step.setdefault(step, []).append(value)
        max_step = max(by_step)
        means = [
            (math.fsum(by_step[step]) / len(by_step[step]) if step in by_step else None)
            for step in range(1, max_step + 1)
        ]
        monotonicity.append(
            {
                "operator": operator,
                "candidate": MULTIRES_ID,
                "per_step_mean_metric": means,
                **pb.ladder_monotonicity(means),
            }
        )

    receipt = {
        "schema": "torchsynth-perceptual-benchmark",
        "schema_version": 1,
        "benchmark_version": pb.BENCHMARK_VERSION,
        "status": "EXECUTED-NO-HUMAN-DATA",
        "doctrine": pb.DOCTRINE,
        "human_comparison": pb.human_comparison_row(),
        "pins": pb.CANDIDATE_PINS,
        "limitations": pb.CANDIDATE_LIMITATIONS,
        "availability": {
            identity: availability[identity] for identity in candidate_ids
        },
        "provenance": {
            "config_id": config["config_id"],
            "config_identity_sha256": config_id,
            "manifest": str(args.manifest),
            "manifest_sha256": sha256_hex(manifest_bytes),
            "estimator_receipt": str(args.estimator_receipt),
            "estimator_receipt_sha256": estimator_sha,
            "benchmark_module_sha256": sha256_hex(Path(pb.__file__).read_bytes()),
            "bootstrap_trials": args.bootstrap_trials,
            "seed": args.seed,
            "accumulation": pb.ACCUMULATION,
            "python": sys.version.split()[0],
            "numpy": _numpy_version(),
        },
        "binding": {
            "pairs_rederived_and_verified": verified,
            "references_verified": len(references),
            "note": (
                "every benchmarked pair was re-derived from local reference "
                "audio and verified byte-exact against the committed session "
                "manifest before any candidate saw it"
            ),
        },
        "stimuli": stimulus_rows,
        "reference_dependence": reference_dependence,
        "resampler_contribution": resampler_rows,
        "agreement": agreement,
        "monotonicity_per_operator": monotonicity,
        "ladder_per_case": per_case,
        "coverage": {
            "families_present": sorted({str(r["family"]) for r in stimulus_rows}),
            "host_run_gated_note": (
                "session 01 renders only corpus-audio operators; 22 "
                "host-run-gated operators are skipped rows in the manifest "
                "and have no candidate rows here"
            ),
        },
        "custody": {
            "receipts_only": True,
            "policy": (
                "stimulus audio stays in the operator's local session "
                "directory; this receipt carries rows and identities only"
            ),
        },
    }
    args.out.write_bytes(canonical_bytes(receipt))
    print("receipt: %s" % args.out)
    print(
        "pairs verified: %d; multires rows executed: %d; human row: %s"
        % (verified, executed_multires, receipt["human_comparison"]["verdict"])
    )
    return 0


def _numpy_version():
    try:
        import numpy
    except Exception:
        return None
    return numpy.__version__


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BenchmarkRefusal, CustodyRefusal, pb.PerceptualBenchmarkError) as error:
        print("refusal: %s" % error, file=sys.stderr)
        sys.exit(2)
