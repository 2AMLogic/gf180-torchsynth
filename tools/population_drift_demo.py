#!/usr/bin/env python3
"""Population-drift conditions over a landed corpus store; receipts only (issue 46).

Demonstrates the declared OBS-section conditions of the Startable Subset on a
rendered corpus:

- identity-permutation negative control (population metrics must not move);
- within-corpus 50/50 split baseline (the OBS partition protocol);
- extreme-pitch condition (decimation x2: crude octave-up, the OBS pathology
  analog) as a cross-population drift demonstration;
- noise-gain condition (seeded white noise mixed at a declared amplitude);
- wrong-nebula / parameter-distribution stand-in (stratum-biased resample);
- outlier injection over a declared severity grid k in {1, 4, 16}: the
  trailing k corpus entries are replaced by entries of a declared outlier
  pool (scaled noise-gain, amplitude 2x the noise-gain condition) and the
  FAD/MMD contamination response is measured, with a FAD-infinity curve of
  the clean corpus against the maximally injected population;
- FAD-infinity sample-size curve with bias/uncertainty.

Everything here is DEMONSTRATION EVIDENCE ONLY: distributions answer population
questions, never per-sound identity or correctness; no acceptance verdict, no
threshold, and never an optimization target. Embeddings use the provisional
stdlib envelope (NOT the OBS OpenL3 pin) unless the pinned dependency is
installed and --use-openl3 is passed. Stdlib only; never renders, repairs, or
writes anywhere but the declared receipt path. The narrow role decision
(2026-09-20) is recorded in spec/POPULATION-METRICS.md: corpus-level auxiliary
diagnostics only; alerting is flag-only with no gating authority.
"""

import argparse
import array
import json
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.artifact_renderer import digest, json_bytes  # noqa: E402
from torchsynth_voice.population_metrics import (  # noqa: E402
    PROVISIONAL_ENVELOPE_V0,
    REFERENCE_EMBEDDING_PIN,
    inject_outliers,
    openl3_availability,
    provisional_envelope_embedding,
    run_population_conditions,
)

SCHEMA = "torchsynth-population-drift-demo"
SCHEMA_VERSION = 2
AUDIO_BYTES = 176400 * 4
EXPECTED_SAMPLES = 176400
SAMPLE_RATE = 44100
NOISE_AMPLITUDE = 0.25
OUTLIER_NOISE_AMPLITUDE = 0.5
OUTLIER_POOL_NAME = "outlier_pool_noise_gain_x2"
WINDOWS = 8


def load_population(store: Path):
    indexes = sorted((store / "indexes").glob("*.json"))
    if len(indexes) != 1:
        raise SystemExit(
            f"population-drift demo: expected exactly one store index, found {len(indexes)}"
        )
    index_bytes = indexes[0].read_bytes()
    index = json.loads(index_bytes)
    if index.get("status") != "complete":
        raise SystemExit("population-drift demo: store index is not status=complete")
    cases = index.get("cases", [])
    clips = []
    per_case_hashes = []
    for case in cases:
        if case.get("status") != "complete" or case.get("split") != "development":
            continue
        artifact_dir = store / "artifacts" / case["artifact"]["artifact_id"]
        audio_path = artifact_dir / "audio.f32le"
        raw = audio_path.read_bytes()
        if len(raw) != AUDIO_BYTES:
            raise SystemExit(
                f"population-drift demo: {audio_path} is {len(raw)} bytes, expected {AUDIO_BYTES}"
            )
        samples = array.array("f")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        for value in samples:
            if value != value or value in (float("inf"), float("-inf")):
                raise SystemExit(
                    f"population-drift demo: nonfinite sample in {audio_path}"
                )
        clips.append(list(samples))
        per_case_hashes.append([case["case_id"], digest(raw)])
    if not clips:
        raise SystemExit(
            "population-drift demo: no complete development cases in store"
        )
    combined = digest("".join(h for _, h in per_case_hashes).encode())
    return (
        clips,
        {
            "index_path": str(indexes[0]),
            "index_sha256": digest(index_bytes),
            "case_count": len(clips),
            "per_case_audio_sha256": per_case_hashes,
            "combined_audio_sha256": combined,
            "expected_samples_per_case": EXPECTED_SAMPLES,
            "sample_rate": SAMPLE_RATE,
        },
        index,
    )


def git_context():
    context = {"commit": None, "clean": None}
    try:
        context["commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
        context["clean"] = dirty == ""
    except Exception:
        pass
    return context


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, type=Path, help="corpus store root")
    parser.add_argument(
        "--receipt", required=True, type=Path, help="receipt output path"
    )
    parser.add_argument("--seed", type=int, default=20260920)
    parser.add_argument(
        "--use-openl3",
        action="store_true",
        help="use the pinned OpenL3 producer (requires openl3+torch); "
        "otherwise the provisional stdlib embedding is declared instead",
    )
    parser.add_argument(
        "--fadinfty-sizes", default="8,16,24,32,48,64,96", help="sample-size curve"
    )
    parser.add_argument("--fadinfty-trials", type=int, default=20)
    parser.add_argument(
        "--outlier-sizes",
        default="1,4,16",
        help="declared outlier-injection severity grid (corpus entries replaced)",
    )
    parser.add_argument(
        "--generated-utc",
        default=None,
        help="declared generation timestamp (ISO 8601); default is the current "
        "time. Pass one fixed value to make two runs byte-identical (the "
        "receipt determinism check).",
    )
    args = parser.parse_args()

    clips, store_receipt, _index = load_population(args.store)

    if args.use_openl3:
        probe = openl3_availability()
        if not probe["available"]:
            raise SystemExit(f"population-drift demo: {probe['reason']}")
        from torchsynth_voice.population_metrics import openl3_reference_embeddings

        embeddings = openl3_reference_embeddings(clips, sample_rate=SAMPLE_RATE)
        embedding_block = {
            "identity": REFERENCE_EMBEDDING_PIN["identity"],
            "pin": dict(REFERENCE_EMBEDDING_PIN),
        }
    else:
        embeddings = [
            provisional_envelope_embedding(clip, windows=WINDOWS) for clip in clips
        ]
        embedding_block = {
            "identity": PROVISIONAL_ENVELOPE_V0["identity"],
            "provisional": dict(PROVISIONAL_ENVELOPE_V0),
            "obs_reference_pin": dict(REFERENCE_EMBEDDING_PIN),
            "openl3_available": openl3_availability()["available"],
            "note": (
                "provisional stdlib embedding because the pinned dependency is "
                "absent; machinery demonstration only"
            ),
        }

    pitch_x2 = [clip[::2] for clip in clips]
    noise_rng = random.Random(args.seed + 1)
    noise_gain = [
        [s + (noise_rng.random() * 2.0 - 1.0) * NOISE_AMPLITUDE for s in clip]
        for clip in clips
    ]
    outlier_rng = random.Random(args.seed + 3)
    outlier_pool_clips = [
        [
            s + (outlier_rng.random() * 2.0 - 1.0) * OUTLIER_NOISE_AMPLITUDE
            for s in clip
        ]
        for clip in clips
    ]
    outlier_sizes = [int(s) for s in args.outlier_sizes.split(",") if s.strip()]
    max_k = max(outlier_sizes)

    populations = {
        "corpus": embeddings,
        "corpus_pitch_x2": [
            provisional_envelope_embedding(clip, windows=WINDOWS) for clip in pitch_x2
        ],
        "corpus_noise_gain": [
            provisional_envelope_embedding(clip, windows=WINDOWS) for clip in noise_gain
        ],
        OUTLIER_POOL_NAME: [
            provisional_envelope_embedding(clip, windows=WINDOWS)
            for clip in outlier_pool_clips
        ],
    }
    injected_max_name = f"corpus_outlier_k{max_k}"
    populations[injected_max_name] = inject_outliers(
        populations["corpus"], populations[OUTLIER_POOL_NAME], max_k
    )

    sizes = [int(s) for s in args.fadinfty_sizes.split(",") if s.strip()]
    conditions = [
        {"kind": "permutation_control", "population": "corpus"},
        {"kind": "split_half", "population": "corpus"},
        {
            "kind": "cross_population",
            "a": "corpus",
            "b": "corpus_pitch_x2",
            "label": "extreme-pitch (decimation x2 octave-up analog)",
        },
        {
            "kind": "cross_population",
            "a": "corpus",
            "b": "corpus_noise_gain",
            "label": f"noise-gain (seeded white noise +-{NOISE_AMPLITUDE})",
        },
        {
            "kind": "parameter_shift_biased_resample",
            "population": "corpus",
            "label": "wrong-nebula / parameter-distribution stand-in (biased resample)",
        },
        {
            "kind": "outlier_injection",
            "population": "corpus",
            "outlier_pool": OUTLIER_POOL_NAME,
            "injection_sizes": outlier_sizes,
            "label": (
                f"outlier injection (scaled noise-gain pool, amplitude "
                f"+-{OUTLIER_NOISE_AMPLITUDE})"
            ),
        },
        {
            "kind": "fadinfty",
            "population": "corpus",
            "sample_sizes": sizes,
            "trials": args.fadinfty_trials,
            "seed": args.seed + 2,
        },
        {
            "kind": "fadinfty",
            "reference": "corpus",
            "population": injected_max_name,
            "sample_sizes": sizes,
            "trials": args.fadinfty_trials,
            "seed": args.seed + 4,
        },
    ]

    results = run_population_conditions(
        populations, conditions=conditions, seed=args.seed
    )

    receipt = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "issue": 46,
        "generated_utc": args.generated_utc or datetime.now(timezone.utc).isoformat(),
        "producer": {"git_context": git_context()},
        "store": store_receipt,
        "embedding": embedding_block,
        "populations": {
            "corpus": {
                "source": "landed development corpus, indices 0-95, store audio.f32le",
                "n": len(embeddings),
            },
            "corpus_pitch_x2": {
                "definition": "every second sample of each corpus clip (octave-up analog on playback at the original rate)",
                "n": len(pitch_x2),
            },
            "corpus_noise_gain": {
                "definition": f"corpus clip plus seeded uniform white noise, amplitude +-{NOISE_AMPLITUDE}, scale relative to float range",
                "seed": args.seed + 1,
                "n": len(noise_gain),
            },
            OUTLIER_POOL_NAME: {
                "definition": (
                    "declared outlier construction rule: corpus clip plus seeded "
                    f"uniform white noise, amplitude +-{OUTLIER_NOISE_AMPLITUDE} "
                    f"(2x the noise-gain condition), scale relative to float range"
                ),
                "seed": args.seed + 3,
                "n": len(populations[OUTLIER_POOL_NAME]),
            },
            injected_max_name: {
                "definition": (
                    f"trailing-{max_k} replacement injection via "
                    f"population_metrics.inject_outliers: the last {max_k} corpus "
                    f"embeddings are replaced by the first {max_k} "
                    f"{OUTLIER_POOL_NAME} embeddings; deterministic, seed-free"
                ),
                "n": len(populations[injected_max_name]),
            },
        },
        "conditions": results["conditions"],
        "provenance": results["provenance"],
        "disclaimers": [
            "DEMONSTRATION EVIDENCE ONLY: population diagnostics answer population questions.",
            "Never per-sound identity or implementation correctness; per-index comparison remains an independent mandatory gate.",
            "No acceptance verdict, no frozen threshold, and never an optimization target (OBS negative-result doctrine, docs/MEASUREMENT-PLAN.md).",
            "The #44-gated corruption-ladder comparator is NOT exercised here; the wrong-nebula condition is a declared biased-resample stand-in.",
            "Outlier injection is a contamination-response demonstration over a declared severity grid: no alerting threshold, no pass/fail, no gating authority.",
            "The narrow role decision (2026-09-20) is recorded in spec/POPULATION-METRICS.md: corpus-level auxiliary diagnostics only; alerting is flag-only; the human-transparency row stays NO VERDICT.",
        ],
    }

    output = args.receipt
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json_bytes(receipt))
    print(f"wrote {output} ({len(json_bytes(receipt))} bytes)")
    control = results["conditions"][0]
    print(f"permutation control value: {control['value']}")
    for entry in results["conditions"]:
        if entry["kind"] == "cross_population":
            print(
                f"{entry['label']}: mmd={entry['mmd']['value']:.6f} "
                f"fad={entry['fad']['value']:.6f}"
            )
        if entry["kind"] == "outlier_injection":
            for row in entry["injections"]:
                print(
                    f"outlier k={row['k']} ({row['contamination_rate']:.4f}): "
                    f"mmd={row['mmd']['value']:.6f} fad={row['fad']['value']:.6f}"
                )
        if entry["kind"] == "fadinfty":
            scope = (
                f"ref={entry['reference']} test={entry['population']}"
                if entry["reference"] != entry["population"]
                else f"self={entry['population']}"
            )
            print(
                f"fadinfty[{scope}]={entry['fadinfty']:.6f} curve="
                + str([round(row["mean_fad"], 6) for row in entry["curve"]])
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
