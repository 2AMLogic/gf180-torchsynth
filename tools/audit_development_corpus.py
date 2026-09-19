#!/usr/bin/env python3
"""Independently audit the fixed-96 development-corpus receipt.

The landed runner/verifier reconciles a run against its own plan; neither
re-derives the expected denominator from outside the run. This audit consumes
only the committed receipt, the checked-in manifest, and (optionally) the raw
store, and re-establishes from public contracts that:

- the expected identity set is exactly {0,...,95}, all development, with no
  holdout identity rendered, referenced, or admitted;
- counts are re-derived from individual case records, not trusted from
  declared totals (a re-counted 95- or 97-case index cannot pass);
- the embedded plan/index bytes hash to the recorded digests, and the manifest
  digest matches the checked-in ``spec/reference/corpus-v0.json`` bytes;
- every complete case carries an artifact reference, and (with ``--store``)
  the landed read-only verifier rehashes metadata/audio/plan/index/journal and
  every audio payload is exactly 176400 finite little-endian float32 samples.

Receipt-only mode verifies internal consistency and hashes; it cannot
re-examine raw bytes. Stdlib only; never writes, renders, or repairs.
"""

import argparse
import json
import math
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.artifact_renderer import digest, json_bytes  # noqa: E402
from torchsynth_voice.corpus import verify_run  # noqa: E402
from torchsynth_voice.artifacts import (  # noqa: E402
    ValidationError,
    loads,
)

SCHEMA = "torchsynth-development-corpus-evidence"
AUDIO_BYTES = 176400 * 4
DEVELOPMENT = list(range(96))
PINNED_SOURCE_COMMIT = "2b0964d4c6c3d472a2a0d54d91b408caaeffca6d"


def require(condition, message):
    if not condition:
        raise ValidationError("development-corpus audit: " + message)


def _recounted(index, expected):
    cases = index["cases"]
    identifiers = [case["case_id"] for case in cases]
    require(
        len(identifiers) == len(set(identifiers)),
        "duplicate case identity in index",
    )
    require(
        [int(i.split("-")[1]) for i in identifiers] == expected,
        "index identities are not exactly the expected development set",
    )
    require(
        all(case["split"] == "development" for case in cases),
        "non-development partition identity in index",
    )
    complete = [case for case in cases if case["status"] == "complete"]
    require(
        all(case["artifact"] is not None for case in complete),
        "complete case without artifact reference",
    )
    require(
        all(case["failures"] == [] and case["warnings"] == [] for case in complete),
        "complete case carries failure/warning entries",
    )
    observed = len(complete)
    require(
        index["expected_case_count"] == len(expected),
        "declared expected count is not the fixed denominator",
    )
    require(
        index["observed_case_count"] == observed,
        "declared observed count disagrees with re-counted case records",
    )
    require(
        index["status"] == ("complete" if observed == len(expected) else "failed"),
        "index status disagrees with re-counted case records",
    )
    return observed


def audit_receipt(receipt, *, store=None, expected=None):
    """Audit the receipt document; with store, additionally verify raw bytes."""
    expected = DEVELOPMENT if expected is None else list(expected)
    require(
        type(receipt) is dict
        and receipt.get("schema") == SCHEMA
        and receipt.get("schema_version") == 1,
        "receipt schema mismatch",
    )
    require(
        receipt["expectation"]["development_indices"] == expected,
        "receipt expectation is not the fixed development set",
    )
    require(
        receipt["expectation"]["holdout_indices_rendered"] == [],
        "receipt records holdout rendering",
    )
    require(
        receipt["manifest_sha256"]
        == digest((ROOT / "spec/reference/corpus-v0.json").read_bytes()),
        "manifest digest differs from the checked-in development manifest",
    )
    require(
        receipt["source_commit"] == PINNED_SOURCE_COMMIT,
        "normative upstream source commit mismatch",
    )
    require(
        receipt["producer_git"]["dirty"] is False,
        "producer checkout was not frozen clean",
    )
    run = receipt["run"]
    require(
        digest(json_bytes(receipt["plan"])) == run["plan_sha256"],
        "embedded plan bytes differ from the recorded plan digest",
    )
    require(
        digest(json_bytes(receipt["index"])) == run["index_sha256"],
        "embedded index bytes differ from the recorded index digest",
    )
    require(
        receipt["plan"]["run_id"] == run["run_id"]
        and receipt["plan"]["manifest"]["sha256"] == receipt["manifest_sha256"],
        "plan linkage mismatch",
    )
    selection = [
        case["sound_index"] for case in receipt["plan"]["selection"]
    ]
    require(
        selection == expected,
        "plan selection is not exactly the expected development set",
    )
    require(
        all(case["split"] == "development" for case in receipt["plan"]["selection"]),
        "plan selection contains non-development partition",
    )
    observed = _recounted(receipt["index"], expected)
    require(
        receipt["index"]["corpus_manifest_sha256"] == receipt["manifest_sha256"],
        "index manifest binding mismatch",
    )
    if store is not None:
        store = Path(store)
        require(
            loads(
                (store / ("indexes/" + run["index_sha256"] + ".json")).read_bytes()
            )
            == receipt["index"],
            "store index bytes differ from the receipt-embedded index",
        )
        envelope = verify_run(
            store, run["run_id"], expected_sha256=run["envelope_sha256"]
        )
        require(
            envelope["counts"]["expected"] == len(expected),
            "run envelope denominator is not the fixed development set",
        )
        require(
            envelope["counts"]["observed"] == observed,
            "run envelope observed count disagrees with the receipt re-count",
        )
        directories = {
            case["artifact"]["artifact_id"]
            for case in receipt["index"]["cases"]
            if case["status"] == "complete"
        }
        require(
            len(directories) == observed,
            "complete cases do not map to unique artifacts",
        )
        for case in receipt["index"]["cases"]:
            if case["status"] != "complete":
                continue
            directory = store / "artifacts" / case["artifact"]["artifact_id"]
            audio = (directory / "audio.f32le").read_bytes()
            require(len(audio) == AUDIO_BYTES, "audio byte count mismatch")
            values = struct.unpack("<%df" % (len(audio) // 4), audio)
            require(
                all(map(math.isfinite, values)),
                "nonfinite sample in " + case["artifact"]["artifact_id"],
            )
    return observed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--receipt",
        type=Path,
        default=ROOT / "sim/reference/development-corpus-first.json",
    )
    parser.add_argument(
        "--store",
        type=Path,
        help="raw artifact store root; enables byte-level re-verification",
    )
    args = parser.parse_args()
    receipt = loads(args.receipt.read_bytes())
    observed = audit_receipt(receipt, store=args.store)
    print(
        json.dumps(
            dict(
                receipt=args.receipt.name,
                store=None if args.store is None else str(args.store),
                expected=len(DEVELOPMENT),
                observed_complete=observed,
                result="PASS" if observed == len(DEVELOPMENT) else "INCOMPLETE",
            ),
            indent=2,
        )
    )
    return 0 if observed == len(DEVELOPMENT) else 1


if __name__ == "__main__":
    raise SystemExit(main())
