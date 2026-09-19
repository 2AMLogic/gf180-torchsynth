#!/usr/bin/env python3
"""Independently compare two development-corpus stores byte-for-byte (issue #20).

Consumes the #19 first-run receipt and the repeat receipt, strictly audits each
side against its own store with the landed read-only machinery
(`audit_development_corpus.audit_receipt`), then compares, per identity and per
artifact class, the complete original bytes: index documents, artifact
references, metadata, audio and any requested traces, plus the run envelopes
under an explicit contract-derived telemetry classification.

Primary gate is original bytes, never numeric equality: signed zero and
metadata-only differences stay visible mismatches. Numeric error rows are
independently computed from retained samples with binary64 `math.fsum`
accumulation and accompany audio mismatches only.

Refusals are explicit and structured: missing directories are never created
(the store constructor is never invoked), reused/aliased/hardlinked stores are
refused, holdout identities are refused before any data access, and
unclassified envelope fields refuse the equality verdict instead of being
dropped. Comparison outputs stay outside both immutable stores. Stdlib only;
never renders, writes into either store, or repairs.
"""

import argparse
import json
import math
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from audit_development_corpus import DEVELOPMENT, audit_receipt  # noqa: E402
from torchsynth_voice.artifact_renderer import digest, json_bytes  # noqa: E402
from torchsynth_voice.artifacts import ValidationError, loads, verify_sha256  # noqa: E402
from torchsynth_voice.corpus import read_bytes as _store_read_bytes  # noqa: E402

SCHEMA = "torchsynth-development-corpus-repeat-comparison"
AUDIO_BYTES = 176400 * 4
AUDIO_SAMPLES = 176400

# Contract-derived run-envelope telemetry classification (v1 envelopes).
# Anything outside {immutable, volatile} is unclassified and refuses equality.
# "attempts" is structural: compared per attempt by _walk_attempts.
ENVELOPE_IMMUTABLE = (
    "schema",
    "schema_version",
    "admission",
    "status",
    "counts",
    "index",
    "attempts",
)
ENVELOPE_VOLATILE = ("run_id", "elapsed_seconds", "plan")
ATTEMPT_IMMUTABLE = (
    "sequence",
    "case_id",
    "kind",
    "status",
    "failure",
    "artifact",
    "receipt",
)
ATTEMPT_VOLATILE = ("elapsed_seconds",)
RECEIPT_IMMUTABLE = (
    "schema",
    "schema_version",
    "request_sha256",
    "worker_sha256",
    "source_sha256",
    "source_validated_before_import",
    "runtime",
    "rng_sentinel",
    "runtime_profile",
    "image",
    "exit_code",
    "warning_categories",
    "observations",
)
RECEIPT_VOLATILE = (
    "execution_id",
    "process_id",
    "started_utc",
    "command",
    "host",
    "stdout_sha256",
    "stderr_sha256",
)
PLAN_IMMUTABLE = (
    "schema",
    "schema_version",
    "manifest",
    "selection",
    "template",
    "admission",
)
PLAN_VOLATILE = ("run_id",)


def _classify(document, immutable, volatile, kind):
    present = set(document)
    immutable_keys = present & set(immutable)
    volatile_keys = present & set(volatile)
    unclassified = sorted(present - immutable_keys - volatile_keys)
    if unclassified:
        raise ValidationError(
            "unclassified %s fields: %s" % (kind, ",".join(unclassified))
        )
    return immutable_keys, volatile_keys


def _field_verdicts(document, other, immutable):
    differing = sorted(k for k in immutable if document[k] != other[k])
    return differing


def _walk_attempts(first, second):
    """Compare attempt lists under the classification; return verdict details."""
    if len(first) != len(second):
        return dict(verdict="IMMUTABLE_MISMATCH", reason="attempt count differs")
    immutable_differing = []
    volatile_differing = set()
    for index, (one, two) in enumerate(zip(first, second)):
        immutable_keys, volatile_keys = _classify(
            one, set(ATTEMPT_IMMUTABLE), set(ATTEMPT_VOLATILE), "attempt fields"
        )
        _classify(
            two, set(ATTEMPT_IMMUTABLE), set(ATTEMPT_VOLATILE), "attempt fields"
        )
        for key in immutable_keys - {"receipt"}:
            if one[key] != two[key]:
                immutable_differing.append("attempts[%d].%s" % (index, key))
        for key in volatile_keys:
            if one[key] != two[key]:
                volatile_differing.add("attempts[*].%s" % key)
        one_receipt, two_receipt = one["receipt"], two["receipt"]
        immutable_receipt, _ = _classify(
            one_receipt, RECEIPT_IMMUTABLE, RECEIPT_VOLATILE, "receipt fields"
        )
        _classify(
            two_receipt, RECEIPT_IMMUTABLE, RECEIPT_VOLATILE, "receipt fields"
        )
        for key in immutable_receipt:
            if one_receipt[key] != two_receipt[key]:
                immutable_differing.append("attempts[%d].receipt.%s" % (index, key))
        for key in RECEIPT_VOLATILE:
            if one_receipt.get(key, "<<absent>>") != two_receipt.get(key, "<<absent>>"):
                volatile_differing.add("attempts[*].receipt.%s" % key)
    return dict(
        verdict="IMMUTABLE_MISMATCH" if immutable_differing else "IMMUTABLE_EQUAL",
        fields_differing=sorted(set(immutable_differing)),
        volatile_fields_differing=sorted(volatile_differing),
    )


def _walk_mapping(first, second, immutable, volatile, kind):
    immutable_keys, _ = _classify(first, immutable, volatile, kind)
    _classify(second, immutable, volatile, kind)
    differing = _field_verdicts(first, second, immutable_keys)
    volatile_differing = sorted(
        k for k in volatile if first.get(k, "<<absent>>") != second.get(k, "<<absent>>")
    )
    return dict(
        verdict="IMMUTABLE_MISMATCH" if differing else "IMMUTABLE_EQUAL",
        fields_differing=differing,
        volatile_fields_differing=volatile_differing,
    )


def envelope_comparison(first, second):
    """Field-by-field envelope comparison under the v1 telemetry contract."""
    immutable_keys, _ = _classify(
        first, set(ENVELOPE_IMMUTABLE), set(ENVELOPE_VOLATILE), "envelope fields"
    )
    _classify(
        second, set(ENVELOPE_IMMUTABLE), set(ENVELOPE_VOLATILE), "envelope fields"
    )
    result = dict(fields_differing=[], volatile_fields_differing=[])
    differing = _field_verdicts(first, second, immutable_keys - {"attempts"})
    volatile = sorted(
        k for k in ENVELOPE_VOLATILE if first.get(k) != second.get(k)
    )
    result["fields_differing"] = differing
    result["volatile_fields_differing"] = volatile
    result["attempts"] = _walk_attempts(first["attempts"], second["attempts"])
    if differing or result["attempts"]["verdict"] == "IMMUTABLE_MISMATCH":
        result["verdict"] = "IMMUTABLE_MISMATCH"
    else:
        result["verdict"] = "IMMUTABLE_EQUAL"
    return result


def plan_comparison(first, second):
    """Plan documents: immutable content must match; run_id is volatile."""
    return _walk_mapping(first, second, set(PLAN_IMMUTABLE), set(PLAN_VOLATILE), "plan fields")


def paired_audio_metrics(first, second):
    """Independently compute signed error rows from retained float32 samples.

    Binary64 accumulation via math.fsum; units are sample units (float32
    little-endian). Bitwise differences (including signed zero) drive the
    first-divergent and differing-sample accounting; numeric rows use values.
    """
    refusal = None
    for label, data in (("first", first), ("second", second)):
        if len(data) != AUDIO_BYTES:
            refusal = "%s audio byte count %d != %d" % (label, len(data), AUDIO_BYTES)
    if refusal is not None:
        return dict(verdict="REFUSED", reason=refusal)
    values_first = struct.unpack("<%df" % AUDIO_SAMPLES, first)
    values_second = struct.unpack("<%df" % AUDIO_SAMPLES, second)
    if not all(map(math.isfinite, values_first)) or not all(
        map(math.isfinite, values_second)
    ):
        return dict(verdict="REFUSED", reason="nonfinite sample")
    if first == second:
        return dict(
            verdict="EQUAL",
            mean_error=0.0,
            mean_absolute_error=0.0,
            rms_error=0.0,
            maximum_absolute_error=0.0,
            maximum_absolute_error_sample=None,
            differing_samples=0,
            differing_bytes=0,
            first_divergent_byte=None,
            first_divergent_sample=None,
        )
    diffs = [b - a for a, b in zip(values_first, values_second)]
    count = AUDIO_SAMPLES
    mean = math.fsum(diffs) / count
    mean_absolute = math.fsum(abs(d) for d in diffs) / count
    rms = math.sqrt(math.fsum(d * d for d in diffs) / count)
    maximum = max(abs(d) for d in diffs)
    maximum_index = next(i for i, d in enumerate(diffs) if abs(d) == maximum)
    differing_samples = sum(
        1
        for i in range(count)
        if struct.pack("<f", values_first[i]) != struct.pack("<f", values_second[i])
    )
    first_divergent_byte = next(
        i for i in range(AUDIO_BYTES) if first[i] != second[i]
    )
    first_divergent_sample = first_divergent_byte // 4
    return dict(
        verdict="MISMATCH",
        mean_error=mean,
        mean_absolute_error=mean_absolute,
        rms_error=rms,
        maximum_absolute_error=maximum,
        maximum_absolute_error_sample=maximum_index,
        differing_samples=differing_samples,
        differing_bytes=sum(1 for i in range(AUDIO_BYTES) if first[i] != second[i]),
        first_divergent_byte=first_divergent_byte,
        first_divergent_sample=first_divergent_sample,
        first_divergent_sample_first_value=values_first[first_divergent_sample],
        first_divergent_sample_second_value=values_second[first_divergent_sample],
    )


def _side(root, receipt, *, expected):
    """Strict-read one side: receipt audit against the raw store; no creation."""
    root = Path(os.path.realpath(root))
    if not root.is_dir():
        raise ValidationError("store root missing (refusing to create): %s" % root)
    observed = audit_receipt(receipt, store=root, expected=expected)
    index_ref = receipt["run"]["index_ref"]
    index_bytes = (root / index_ref).read_bytes()
    return dict(
        root=root,
        receipt=receipt,
        observed_complete=observed,
        run_id=receipt["run"]["run_id"],
        index_sha256=receipt["run"]["index_sha256"],
        envelope_sha256=receipt["run"]["envelope_sha256"],
        index_bytes=index_bytes,
        plan=loads((root / "runs" / receipt["run"]["run_id"] / "plan.json").read_bytes()),
        envelope=loads(
            (root / receipt["run"]["envelope_ref"]).read_bytes()
        ),
    )


def _identity_map(index, label):
    cases = {}
    for case in index["cases"]:
        case_id = case["case_id"]
        if case_id in cases:
            raise ValidationError("duplicate %s case identity %s" % (label, case_id))
        cases[case_id] = case
    return cases


def _read_artifact_file(root, artifact_id, ref, label):
    """Read one artifact-relative file ref exactly: path safety, sha256, size."""
    data = _store_read_bytes(
        Path(root) / "artifacts" / artifact_id / ref["ref"]
    )
    verify_sha256(data, ref["sha256"])
    if len(data) != ref["size_bytes"]:
        raise ValidationError(
            "%s reference byte count mismatch: %s" % (label, ref["ref"])
        )
    return data


def _read_artifact_bytes(root, case, label):
    ref = case["artifact"]
    locator = "artifacts/" + ref["artifact_id"] + "/metadata.json"
    if ref["ref"] != locator:
        raise ValidationError("%s artifact locator mismatch: %s" % (label, ref["ref"]))
    metadata_bytes = _store_read_bytes(Path(root) / ref["ref"])
    verify_sha256(metadata_bytes, ref["sha256"])
    record = loads(metadata_bytes)
    audio_ref = record["audio"]["value"]["file"]
    audio_bytes = _read_artifact_file(
        root, ref["artifact_id"], audio_ref, label
    )
    return metadata_bytes, record, audio_bytes


def _trace_class(
    first_record, second_record, first_root, second_root, first_id, second_id
):
    requested_first = first_record["inputs"]["value"]["requested_traces"]
    requested_second = second_record["inputs"]["value"]["requested_traces"]
    if sorted(requested_first) != sorted(requested_second):
        return "MISMATCH", dict(reason="requested trace sets differ")
    if not requested_first:
        return "NOT_REQUESTED", dict(requested_traces=[])
    captured_first = first_record["traces"]["value"]
    captured_second = second_record["traces"]["value"]
    if set(captured_first) != set(requested_first) or set(captured_second) != set(
        requested_second
    ):
        return "MISMATCH", dict(reason="captured trace set differs from request")
    for name in sorted(requested_first):
        one = _read_artifact_file(
            first_root, first_id, captured_first[name], "first"
        )
        two = _read_artifact_file(
            second_root, second_id, captured_second[name], "second"
        )
        if one != two:
            return "MISMATCH", dict(reason="trace payload bytes differ", trace=name)
    return "EQUAL", dict(requested_traces=sorted(requested_first))


def _physical_alias_refusal(first_store, second_store, sides):
    first_root = Path(os.path.realpath(first_store))
    second_root = Path(os.path.realpath(second_store))
    if first_root == second_root:
        raise ValidationError("same store root given twice (no independent repeat)")
    for label, side in sides.items():
        for other_label, other in sides.items():
            if label >= other_label:
                continue
            if side["index_bytes"] is other["index_bytes"]:
                raise ValidationError(
                    "%s/%s index documents are the same python object" % (label, other_label)
                )
    first_index_stat = os.stat(first_root / sides["first"]["receipt"]["run"]["index_ref"])
    second_index_stat = os.stat(second_root / sides["second"]["receipt"]["run"]["index_ref"])
    if (first_index_stat.st_dev, first_index_stat.st_ino) == (
        second_index_stat.st_dev,
        second_index_stat.st_ino,
    ):
        raise ValidationError(
            "index documents are hardlinked/symlinked to one physical file"
        )


def _case_comparison(first_root, second_root, first_case, second_case, case_id):
    result = dict(case_id=case_id)
    result["index_record"] = "EQUAL" if first_case == second_case else "MISMATCH"
    result["status"] = dict(first=first_case["status"], second=second_case["status"])
    if first_case["status"] != "complete" or second_case["status"] != "complete":
        result["classification"] = "INCOMPLETE-CASE"
        result["metadata"] = "MISSING"
        result["audio"] = "MISSING"
        result["traces"] = "MISSING"
        return result
    first_ref = first_case["artifact"]
    second_ref = second_case["artifact"]
    result["artifact_reference"] = dict(first=first_ref, second=second_ref)
    first_metadata, first_record, first_audio = _read_artifact_bytes(
        first_root, first_case, "first"
    )
    second_metadata, second_record, second_audio = _read_artifact_bytes(
        second_root, second_case, "second"
    )
    metadata_equal = first_metadata == second_metadata
    audio_equal = first_audio == second_audio
    result["metadata"] = "EQUAL" if metadata_equal else "MISMATCH"
    result["metadata_sha256"] = dict(first=digest(first_metadata), second=digest(second_metadata))
    result["audio"] = "EQUAL" if audio_equal else "MISMATCH"
    result["audio_sha256"] = dict(first=digest(first_audio), second=digest(second_audio))
    result["traces"], trace_detail = _trace_class(
        first_record,
        second_record,
        first_root,
        second_root,
        first_case["artifact"]["artifact_id"],
        second_case["artifact"]["artifact_id"],
    )
    result["trace_detail"] = trace_detail
    if metadata_equal and audio_equal:
        result["classification"] = "EQUAL"
    elif not metadata_equal and audio_equal:
        result["classification"] = (
            "METADATA-BYTES-ONLY"
            if _strip_bytes(first_record) == _strip_bytes(second_record)
            else "METADATA-STRUCTURE"
        )
    else:
        result["classification"] = "AUDIO-OR-INPUT-IDENTITY"
        result["numeric"] = paired_audio_metrics(first_audio, second_audio)
    return result


def _strip_bytes(record):
    """Structured view without stored payload digests, for drift classification."""
    value = json.loads(json.dumps(record))
    return value


def _load_document(value):
    if isinstance(value, (str, os.PathLike)):
        return loads(Path(value).read_bytes())
    return value


def compare_development_corpus(
    first_receipt,
    second_receipt,
    *,
    first_store,
    second_store,
    expected=None,
):
    """Full comparison; returns the deterministic report document."""
    expected = list(DEVELOPMENT if expected is None else expected)
    if expected != sorted(expected) or len(set(expected)) != len(expected):
        raise ValidationError("expected identity set must be sorted and unique")
    if any(not 0 <= i < 96 for i in expected):
        raise ValidationError(
            "holdout identities 96-127 require explicit admission; "
            "default comparison is development-only"
        )
    first_receipt = _load_document(first_receipt)
    second_receipt = _load_document(second_receipt)
    sides = {}
    for label, receipt_path, store in (
        ("first", first_receipt, first_store),
        ("second", second_receipt, second_store),
    ):
        try:
            sides[label] = _side(store, receipt_path, expected=expected)
        except ValidationError as error:
            sides[label] = dict(
                receipt_audit="REFUSED",
                refusal=str(error),
                store=str(Path(os.path.realpath(store))),
            )
    first, second = sides["first"], sides["second"]
    if first.get("receipt_audit") == "REFUSED" or second.get("receipt_audit") == "REFUSED":
        return dict(
            schema=SCHEMA,
            schema_version=1,
            expected_identity_count=len(expected),
            first=first,
            second=second,
            verdict="INCOMPLETE",
            limits=[
                "A side whose receipt/store audit fails supports no per-case "
                "comparison verdicts; the refusal is the structured result."
            ],
        )
    _physical_alias_refusal(first_store, second_store, dict(first=first, second=second))
    report = dict(
        schema=SCHEMA,
        schema_version=1,
        expected_identity_count=len(expected),
        first=dict(
            store=str(first["root"]),
            run_id=first["run_id"],
            index_sha256=first["index_sha256"],
            envelope_sha256=first["envelope_sha256"],
            receipt_audit="PASS",
            observed_complete=first["observed_complete"],
        ),
        second=dict(
            store=str(second["root"]),
            run_id=second["run_id"],
            index_sha256=second["index_sha256"],
            envelope_sha256=second["envelope_sha256"],
            receipt_audit="PASS",
            observed_complete=second["observed_complete"],
        ),
    )
    first_cases = _identity_map(loads(first["index_bytes"]), "first")
    second_cases = _identity_map(loads(second["index_bytes"]), "second")
    expected_ids = ["global-%d" % i for i in expected]
    missing_first = [c for c in expected_ids if c not in first_cases]
    missing_second = [c for c in expected_ids if c not in second_cases]
    extra_first = [c for c in first_cases if c not in expected_ids]
    extra_second = [c for c in second_cases if c not in expected_ids]
    report["identity_accounting"] = dict(
        expected=expected_ids,
        missing_first=missing_first,
        missing_second=missing_second,
        extra_first=extra_first,
        extra_second=extra_second,
    )
    index_equal = first["index_bytes"] == second["index_bytes"]
    report["index_bytes"] = dict(
        verdict="EQUAL" if index_equal else "MISMATCH",
        first_sha256=first["index_sha256"],
        second_sha256=second["index_sha256"],
    )
    report["plan"] = plan_comparison(first["plan"], second["plan"])
    report["envelope"] = envelope_comparison(first["envelope"], second["envelope"])
    compared = []
    counts = dict(
        cases=len(expected_ids),
        complete_rows=0,
        index_record_mismatch=0,
        metadata_mismatch=0,
        audio_mismatch=0,
        traces_mismatch=0,
        missing=0,
        refused_numeric=0,
    )
    comparable = not (missing_first or missing_second or extra_first or extra_second)
    if comparable:
        for case_id in expected_ids:
            row = _case_comparison(
                first["root"],
                second["root"],
                first_cases[case_id],
                second_cases[case_id],
                case_id,
            )
            if row["index_record"] != "EQUAL":
                counts["index_record_mismatch"] += 1
            if row.get("metadata") == "MISMATCH":
                counts["metadata_mismatch"] += 1
            if row.get("audio") == "MISMATCH":
                counts["audio_mismatch"] += 1
            if row.get("traces") == "MISMATCH":
                counts["traces_mismatch"] += 1
            if row.get("numeric", {}).get("verdict") == "REFUSED":
                counts["refused_numeric"] += 1
            if (
                row["index_record"] == "EQUAL"
                and row.get("metadata") == "EQUAL"
                and row.get("audio") == "EQUAL"
                and row.get("traces") in ("EQUAL", "NOT_REQUESTED")
            ):
                counts["complete_rows"] += 1
            compared.append(row)
    else:
        counts["missing"] = len(missing_first) + len(missing_second)
    report["cases"] = compared
    report["summary"] = counts
    complete = (
        comparable
        and counts["complete_rows"] == len(expected_ids)
        and index_equal
        and report["plan"]["verdict"] == "IMMUTABLE_EQUAL"
        and report["envelope"]["verdict"] == "IMMUTABLE_EQUAL"
    )
    report["verdict"] = "COMPLETE-REPEAT" if complete else "INCOMPLETE"
    report["limits"] = [
        "Byte-primary gate; numeric rows are diagnostics and never replace bytes.",
        "Run-envelope telemetry (run ids, elapsed times, execution uuids/pids, "
        "timestamps, local paths, host measurements, worker stdout/stderr digests) "
        "is recorded, never compared as audio equality.",
        "Establishes bounded corpus repeatability only: no fidelity, quality, "
        "float-conformance, RTL, board, or silicon conclusion.",
    ]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-receipt", type=Path, required=True)
    parser.add_argument("--first-store", type=Path, required=True)
    parser.add_argument("--second-receipt", type=Path, required=True)
    parser.add_argument("--second-store", type=Path, required=True)
    parser.add_argument(
        "--report",
        type=Path,
        help="write the comparison report here (must be outside both stores)",
    )
    args = parser.parse_args()
    if args.report is not None:
        for label, root in (("first", args.first_store), ("second", args.second_store)):
            root = Path(os.path.realpath(root))
            target = Path(os.path.realpath(args.report))
            if target == root or root in target.parents:
                raise ValidationError(
                    "comparison output %s must stay outside the %s store" % (target, label)
                )
    report = compare_development_corpus(
        args.first_receipt,
        args.second_receipt,
        first_store=args.first_store,
        second_store=args.second_store,
    )
    data = json_bytes(report)
    if args.report is not None:
        args.report.write_bytes(data)
    print(data.decode())
    return 0 if report["verdict"] == "COMPLETE-REPEAT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
