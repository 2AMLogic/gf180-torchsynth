"""Golden-vector file schema, loader, and first-mismatch reporter (issue #68).

The vector format follows #54's acceptance-criteria shape: inputs
(name-keyed parameters), expected traces/output, cycle/sample mapping, and
hashes. Trace names are validated against the canonical trace registry
(``spec/reference/trace-registry-v1.json``); parameter names against the
default-Voice parameter inventory (all 78 canonical names in
``spec/reference/parameter-inventory-v1.json``); clip timing against the
canonical profile (176,400 samples @ 44.1 kHz audio / 441 Hz control, the
registry's 4-second default-nebula clip).

Version policy (#68 AC: "interface changes fail contract/version checks
rather than silently recompiling"): a vector file declares the trace-registry
semantic version and the parameter-inventory source commit it was generated
against. The loader refuses any mismatch with the live spec files. Real
(contract-bound) vectors additionally carry accepted-contract digests in
their provenance (constants package, choice register, and — via the frozen
release receipt — the DR-0008 record); :func:`verify_accepted_contract` is
the tb flow's refusal check over that binding.

Comparison policy: the reporter compares with exact equality. Per AGENTS.md,
fixed-point model to RTL is sample-exact; declared error metrics belong to
the float-to-fixed model comparison, never here. No tolerance is applied and
none is configurable.

The schema is content-agnostic about numeric widths: no width appears here.
Widths for generated RTL constants come only from the DR-0008 choice
register via its refusal gate (see
``torchsynth_voice.fixedpoint.codegen``), which refuses every entry today.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "spec/reference/trace-registry-v1.json"
INVENTORY_PATH = ROOT / "spec/reference/parameter-inventory-v1.json"

#: Accepted-contract artifacts the vector files are hash-linked to (issue #68
#: AC: generated/handwritten constants hash-linked to the accepted numeric
#: contract; interface changes fail the check instead of silently recompiling).
SENTINEL_VECTOR_PATH = ROOT / "sim/reference/golden-vector-fixed-anchor.json"
RECEIPT_PATH = ROOT / "sim/reference/fixed-voice-golden-v1.json"
CONSTANTS_PACKAGE_PATH = ROOT / "tb/sv/gf180_rtl_constants_pkg.sv"
CHOICES_REGISTER_PATH = ROOT / "spec/reference/fixedpoint-choices-v1.json"
DR_0008_RECORD_PATH = ROOT / "spec/decision-records/0008-fixed-point-numeric-contract.md"

VECTOR_SCHEMA = "gf180-torchsynth/golden-vector-v1"
SCHEMA_VERSION = 1

#: Canonical one-shot clip profile (DR-0002 offline 4 s @ 44.1 kHz).
CANONICAL_PROFILE = "canonical-default-nebula-4s"
CANONICAL_SAMPLE_RATE_HZ = 44100
CANONICAL_SAMPLE_COUNT = 176400
CANONICAL_CONTROL_RATE_HZ = 441
CANONICAL_CONTROL_COUNT = 1764

#: Reduced self-consistent profile for harness bring-up (synthetic DUTs only).
SYNTHETIC_PROFILE = "synthetic-bringup"

ENCODINGS = ("f32le", "synthetic-int")

MISMATCH_COLUMNS = ("cycle", "sample", "trace", "expected", "actual")


class VectorError(ValueError):
    """Raised when a golden-vector file fails schema or version checks."""


class Mismatch:
    """First observed disagreement between expected and captured values."""

    __slots__ = ("cycle", "sample", "trace", "expected", "actual")

    def __init__(
        self,
        cycle: int,
        sample: int,
        trace: str,
        expected: Any,
        actual: Any,
    ) -> None:
        self.cycle = cycle
        self.sample = sample
        self.trace = trace
        self.expected = expected
        self.actual = actual

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mismatch):
            return NotImplemented
        return (
            self.cycle == other.cycle
            and self.sample == other.sample
            and self.trace == other.trace
            and self.expected == other.expected
            and self.actual == other.actual
        )

    def __repr__(self) -> str:
        return (
            "Mismatch(cycle=%r, sample=%r, trace=%r, expected=%r, actual=%r)"
            % (self.cycle, self.sample, self.trace, self.expected, self.actual)
        )

    def row(self) -> str:
        """Single data line with the mandated five columns."""
        return "cycle=%s sample=%s trace=%s expected=%s actual=%s" % (
            self.cycle,
            self.sample,
            self.trace,
            self.expected,
            self.actual,
        )


def format_mismatch(mismatch: Mismatch) -> str:
    """Render the first-mismatch report with the mandated column header."""
    header = " | ".join(MISMATCH_COLUMNS)
    row = " | ".join(
        [
            str(mismatch.cycle),
            str(mismatch.sample),
            mismatch.trace,
            str(mismatch.expected),
            str(mismatch.actual),
        ]
    )
    return "first mismatch:\n  %s\n  %s" % (header, row)


def compute_content_hash(document: Dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON of everything except the hash field."""
    payload = {k: v for k, v in document.items() if k != "content_hash"}
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def sha256_file(path: Union[str, Path]) -> str:
    """SHA-256 over the file's raw bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_accepted_contract(
    vector: Dict[str, Any],
    constants_path: Union[str, Path, None] = None,
    register_path: Union[str, Path, None] = None,
    record_path: Union[str, Path, None] = None,
    receipt_path: Union[str, Path, None] = None,
) -> Dict[str, str]:
    """Contract check binding a vector file to the accepted DR-0008 contract.

    Issue #68 AC: generated constants are hash-linked to the accepted numeric
    contract, and interface changes fail contract/version checks rather than
    silently recompiling. This is the tb flow's consumer side of that link:
    it refuses (raises :class:`VectorError`) unless

    - the vector's provenance declares DR-0008 ``Accepted``;
    - the declared constants-package digest matches the live package bytes;
    - the declared choice-register digest matches the live register bytes;
    - the accepted DR-0008 record digest matches the live record — taken
      from the vector provenance when present, otherwise from the frozen
      release receipt's ``bindings`` (the two committed #54 artifacts bind
      the three digests between them).

    Returns the map of verified live bindings. Every refusal names the
    stale artifact and says "regenerate", never "recompile".
    """
    provenance = vector.get("provenance")
    _require(
        isinstance(provenance, dict),
        "vector carries no provenance object; nothing is contract-bound",
    )
    status = provenance.get("dr_0008_status")
    _require(
        isinstance(status, str) and status.startswith("Accepted"),
        "contract refusal: vector provenance declares dr_0008_status %r, "
        "not Accepted; refusing to consume it against the numeric contract"
        % (status,),
    )

    constants_path = constants_path or CONSTANTS_PACKAGE_PATH
    register_path = register_path or CHOICES_REGISTER_PATH
    record_path = record_path or DR_0008_RECORD_PATH
    receipt_path = receipt_path or RECEIPT_PATH

    verified: Dict[str, str] = {}
    declared_package = provenance.get("dr_0008_constants_package_sha256")
    _require(
        isinstance(declared_package, str) and declared_package,
        "vector provenance must declare dr_0008_constants_package_sha256; "
        "regenerate the vector against the accepted register",
    )
    live_package = sha256_file(constants_path)
    _require(
        declared_package == live_package,
        "contract refusal: constants package %s hashes to %s but the vector "
        "was generated against %s; regenerate the constants (and the vectors "
        "derived from them) instead of silently recompiling"
        % (constants_path, live_package, declared_package),
    )
    verified["constants_package_sha256"] = live_package

    declared_register = provenance.get("choices_register_sha256")
    _require(
        isinstance(declared_register, str) and declared_register,
        "vector provenance must declare choices_register_sha256; "
        "regenerate the vector against the accepted register",
    )
    live_register = sha256_file(register_path)
    _require(
        declared_register == live_register,
        "contract refusal: choice register %s hashes to %s but the vector "
        "was generated against %s; regenerate the vector instead of "
        "silently recompiling" % (register_path, live_register, declared_register),
    )
    verified["choices_register_sha256"] = live_register

    declared_record = provenance.get("dr_0008_record_sha256")
    if isinstance(declared_record, str) and declared_record:
        verified["dr_0008_record_source"] = "vector provenance"
    else:
        try:
            receipt = json.loads(
                Path(receipt_path).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise VectorError(
                "vector provenance declares no dr_0008_record_sha256 and the "
                "release receipt %s is unreadable (%s); cannot bind the "
                "accepted record" % (receipt_path, error)
            ) from error
        declared_record = (receipt.get("bindings") or {}).get(
            "dr_0008_record_sha256"
        )
        _require(
            isinstance(declared_record, str) and declared_record,
            "neither the vector provenance nor the receipt bindings declare "
            "dr_0008_record_sha256; cannot bind the accepted record",
        )
        verified["dr_0008_record_source"] = "receipt bindings"

    live_record = sha256_file(record_path)
    _require(
        declared_record == live_record,
        "contract refusal: DR-0008 record %s hashes to %s but the bound "
        "digest is %s; the accepted contract changed — regenerate the "
        "constants and vectors instead of silently recompiling"
        % (record_path, live_record, declared_record),
    )
    verified["dr_0008_record_sha256"] = live_record
    return verified


def load_registry_document() -> Dict[str, Any]:
    with open(REGISTRY_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_inventory_document() -> Dict[str, Any]:
    with open(INVENTORY_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise VectorError(message)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_clip(clip: Any) -> None:
    _require(isinstance(clip, dict), "clip must be an object")
    profile = clip.get("profile")
    for key in (
        "sample_rate_hz",
        "sample_count",
        "control_rate_hz",
        "control_count",
    ):
        _require(_is_int(clip.get(key)), "clip.%s must be an integer" % key)
    if profile == CANONICAL_PROFILE:
        _require(
            clip["sample_rate_hz"] == CANONICAL_SAMPLE_RATE_HZ
            and clip["sample_count"] == CANONICAL_SAMPLE_COUNT
            and clip["control_rate_hz"] == CANONICAL_CONTROL_RATE_HZ
            and clip["control_count"] == CANONICAL_CONTROL_COUNT,
            "clip profile %r must carry the canonical 4 s / 44.1 kHz timing "
            "(%d samples @ %d Hz, %d control @ %d Hz)"
            % (
                profile,
                CANONICAL_SAMPLE_COUNT,
                CANONICAL_SAMPLE_RATE_HZ,
                CANONICAL_CONTROL_COUNT,
                CANONICAL_CONTROL_RATE_HZ,
            ),
        )
    elif profile == SYNTHETIC_PROFILE:
        _require(clip["sample_rate_hz"] > 0, "synthetic clip needs a positive sample rate")
        _require(clip["sample_count"] > 0, "synthetic clip needs a positive sample count")
        _require(clip["control_rate_hz"] > 0, "synthetic clip needs a positive control rate")
        _require(
            clip["sample_rate_hz"] % clip["control_rate_hz"] == 0,
            "synthetic clip control rate must divide the sample rate",
        )
    else:
        raise VectorError(
            "unknown clip profile %r (expected %r or %r)"
            % (profile, CANONICAL_PROFILE, SYNTHETIC_PROFILE)
        )


def _validate_traces(
    vector: Dict[str, Any], registry: Dict[str, Any]
) -> None:
    registry_meta = {}
    for entry in registry["traces"]:
        registry_meta[entry["name"]] = entry
    traces = vector.get("traces")
    _require(
        isinstance(traces, list) and traces,
        "vector must carry a non-empty traces list",
    )
    clip = vector["clip"]
    seen = set()
    for trace in traces:
        _require(isinstance(trace, dict), "every trace entry must be an object")
        name = trace.get("name")
        _require(
            isinstance(name, str) and name in registry_meta,
            "unknown trace name %r: not in the canonical trace registry" % (name,),
        )
        _require(name not in seen, "duplicate trace name %r" % name)
        seen.add(name)
        meta = registry_meta[name]
        _require(
            trace.get("kind") == meta["kind"],
            "trace %r kind %r disagrees with registry kind %r"
            % (name, trace.get("kind"), meta["kind"]),
        )
        encoding = trace.get("encoding")
        _require(
            encoding in ENCODINGS,
            "trace %r encoding %r outside supported set %r" % (name, encoding, ENCODINGS),
        )
        _require(
            _is_int(trace.get("cycle_start")) and trace["cycle_start"] >= 0,
            "trace %r cycle_start must be a non-negative integer" % name,
        )
        values = trace.get("values")
        _require(
            isinstance(values, list) and len(values) >= 1,
            "trace %r must carry a non-empty values list" % name,
        )
        for value in values:
            if encoding == "synthetic-int":
                _require(
                    _is_int(value),
                    "trace %r synthetic-int values must be integers" % name,
                )
            else:
                _require(
                    _is_int(value) or isinstance(value, float),
                    "trace %r values must be numbers" % name,
                )
        if meta["kind"] == "scalar":
            _require(
                len(values) == 1,
                "scalar trace %r must carry exactly one value" % name,
            )
        elif clip["profile"] == CANONICAL_PROFILE and meta["sample_count"] is not None:
            _require(
                len(values) == meta["sample_count"],
                "canonical-profile trace %r must carry the full registry "
                "sample_count (%d), got %d"
                % (name, meta["sample_count"], len(values)),
            )
        else:
            _require(
                len(values) <= clip["sample_count"],
                "trace %r length %d exceeds the clip sample_count %d"
                % (name, len(values), clip["sample_count"]),
            )


def _validate_parameters(
    vector: Dict[str, Any], inventory: Dict[str, Any]
) -> None:
    known = {row["name"] for row in inventory["parameters"]}
    parameters = vector.get("parameters", {})
    _require(isinstance(parameters, dict), "parameters must be an object")
    unknown = sorted(set(parameters) - known)
    _require(
        not unknown,
        "unknown parameter names %r: not in the %d-name canonical inventory"
        % (unknown, len(known)),
    )
    for name, value in parameters.items():
        _require(
            _is_int(value) or isinstance(value, float),
            "parameter %r must carry a numeric value" % name,
        )


def _validate_versions(
    vector: Dict[str, Any],
    registry: Dict[str, Any],
    inventory: Dict[str, Any],
) -> None:
    declared = vector.get("trace_registry_version")
    _require(
        declared == registry["semantic_version"],
        "version mismatch: vector declares trace_registry_version %r but the "
        "live registry is %r; regenerate instead of recompiling"
        % (declared, registry["semantic_version"]),
    )
    declared_commit = vector.get("parameter_inventory_commit")
    _require(
        declared_commit == inventory["source"]["commit"],
        "version mismatch: vector declares parameter_inventory_commit %r but "
        "the live inventory source commit is %r"
        % (declared_commit, inventory["source"]["commit"]),
    )


def load_vector(
    source: Union[str, Path, Dict[str, Any]],
    registry: Optional[Dict[str, Any]] = None,
    inventory: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Load, version-check, and structurally validate one golden-vector file.

    ``source`` is a path to a JSON file or an already-parsed document.
    Returns the validated document. Raises :class:`VectorError` on any
    schema, name, timing, version, or hash failure.
    """
    if isinstance(source, dict):
        document = source
    else:
        with open(source, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
    _require(isinstance(document, dict), "vector document must be an object")
    _require(
        document.get("schema") == VECTOR_SCHEMA,
        "unsupported vector schema %r (expected %r)"
        % (document.get("schema"), VECTOR_SCHEMA),
    )
    _require(
        document.get("schema_version") == SCHEMA_VERSION,
        "unsupported vector schema_version %r" % (document.get("schema_version"),),
    )

    registry = registry if registry is not None else load_registry_document()
    inventory = inventory if inventory is not None else load_inventory_document()
    _validate_versions(document, registry, inventory)
    _validate_clip(document.get("clip"))
    _validate_traces(document, registry)
    _validate_parameters(document, inventory)

    declared_hash = document.get("content_hash")
    _require(isinstance(declared_hash, str), "vector must carry a content_hash")
    actual_hash = compute_content_hash(document)
    _require(
        declared_hash == actual_hash,
        "content_hash mismatch: declared %s, computed %s (file was tampered "
        "with or hashed by a different schema version)"
        % (declared_hash, actual_hash),
    )
    return document


def first_mismatch(
    expected: Dict[str, Any], actual_by_trace: Dict[str, Sequence[Any]]
) -> Optional[Mismatch]:
    """Scan traces in vector order; return the first disagreement or ``None``.

    ``actual_by_trace`` maps trace name to captured values in cycle order.
    A captured sequence shorter or longer than the expected one is itself a
    mismatch at the first index where they disagree, with the absent side
    reported as ``None``.
    """
    for trace in expected["traces"]:
        name = trace["name"]
        if name not in actual_by_trace:
            raise VectorError("no captured values supplied for trace %r" % name)
        expected_values = trace["values"]
        actual_values = list(actual_by_trace[name])
        cycle_start = trace["cycle_start"]
        for index in range(max(len(expected_values), len(actual_values))):
            expected_value = expected_values[index] if index < len(expected_values) else None
            actual_value = actual_values[index] if index < len(actual_values) else None
            if expected_value != actual_value or type(expected_value) is not type(actual_value):
                return Mismatch(
                    cycle=cycle_start + index,
                    sample=index,
                    trace=name,
                    expected=expected_value,
                    actual=actual_value,
                )
    return None
