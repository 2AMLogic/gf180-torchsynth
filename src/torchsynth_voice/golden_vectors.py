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

Sidecar codec: :func:`pack_words_f32le` / :func:`unpack_words_f32le` are the
``f32le`` encoding this schema names in :data:`ENCODINGS` -- Q-format words
carried losslessly through division by ``2**frac_bits`` (every
``word / 2**frac_bits`` with the signed word inside ``frac_bits + 23`` bits
is exactly representable in binary32); unpacking refuses any value that does
not round-trip to the exact word. They live here, beside the schema that
names the encoding, rather than in any one engine's golden module: the
per-issue mirrors (``mod_matrix_golden``, ``vco2_golden``) re-export them
under their existing spellings via import aliases (issues #247/#455/#461).

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

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Union,
)

from .digest import digest_words, sha256_bytes, sha256_file

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

#: Provenance values every frozen-model case generator declares verbatim.
#: Hand-typing them per generator is how one copy drifts silently.
MODEL_IDENTITY = "fixed-voice-v1 (frozen composition control path, PR #156)"
DR_0008_STATUS = "Accepted (reviewed merge; 2026-09-21)"

#: The two VCO lanes' committed provenance strings (#73/#74), shared so one
#: lane cannot drift from the other. Deliberately NOT ``MODEL_IDENTITY`` /
#: ``DR_0008_STATUS``: those carry the control-path text, and rewriting the
#: VCO vectors' provenance would change committed bytes across all 42 VCO
#: vector files (out of scope for #535; the strings stay byte-identical).
VCO_MODEL_IDENTITY = "fixed-voice-v1"
VCO_DR_0008_STATUS = "Accepted"

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


def load_frozen_physical(directed_path: Path, case_id: str) -> Dict[str, Any]:
    """The frozen receipt case's public physical map (all consumed names)."""

    directed = json.loads(directed_path.read_text(encoding="utf-8"))
    base = {name: entry["physical"] for name, entry in directed["base"].items()}
    by_id = {case["id"]: case for case in directed["cases"]}
    case = by_id[case_id]
    physical = dict(base)
    for name, override in case["overrides"].items():
        physical[name] = override["physical"]
    return physical


def apply_overrides(physical: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(physical)
    merged.update(overrides)
    return merged


def frozen_receipt_record_digest(receipt_path: Path = RECEIPT_PATH) -> str:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    return receipt["bindings"]["dr_0008_record_sha256"]


def frozen_receipt_binding(
    document: Dict[str, Any], case_id: str, receipt_path: Path = RECEIPT_PATH
) -> Dict[str, Any]:
    """Re-derive the frozen receipt's trace digests for one document."""

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    case = next(c for c in receipt["cases"] if c["id"] == case_id)
    digests = {}
    for trace in document["traces"]:
        name = trace["name"]
        declared = case["traces"][name]
        blob = json.dumps(
            trace["values"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        actual = hashlib.sha256(blob).hexdigest()
        if actual != declared:
            raise SystemExit(
                "FROZEN RECEIPT DRIFT: %s digests to %s but the frozen "
                "receipt (#54, PR #156) declares %s — the model or its "
                "inputs moved; regenerate, do not overwrite the binding"
                % (name, actual, declared)
            )
        digests[name] = declared
    return {
        "receipt": "sim/reference/fixed-voice-golden-v1.json",
        "case_id": case_id,
        "trace_digests": digests,
        "verified": True,
    }


def canonical_clip() -> Dict[str, Any]:
    """The five-key canonical clip block every committed vector carries."""

    return {
        "profile": CANONICAL_PROFILE,
        "sample_rate_hz": CANONICAL_SAMPLE_RATE_HZ,
        "sample_count": CANONICAL_SAMPLE_COUNT,
        "control_rate_hz": CANONICAL_CONTROL_RATE_HZ,
        "control_count": CANONICAL_CONTROL_COUNT,
    }


def frozen_provenance(
    generator: str,
    issue: int,
    derivation: str,
    case_id: str,
    frozen_receipt: Optional[Dict[str, Any]] = None,
    receipt_path: Path = RECEIPT_PATH,
) -> Dict[str, Any]:
    """Provenance for one case rendered through the frozen fixed model.

    ``generator``, ``issue`` and ``derivation`` are the generator's own;
    the model identity, the DR-0008 status string and the three accepted
    contract digests are shared, so they are read from this module rather
    than hand-typed per tool. A generator needing extra keys (audio
    evidence rows, matrix counters) adds them to the returned dict.
    """

    provenance: Dict[str, Any] = {
        "generator": generator,
        "issue": issue,
        "model_identity": MODEL_IDENTITY,
        "dr_0008_status": DR_0008_STATUS,
        "dr_0008_constants_package_sha256": sha256_file(CONSTANTS_PACKAGE_PATH),
        "choices_register_sha256": sha256_file(CHOICES_REGISTER_PATH),
        "dr_0008_record_sha256": frozen_receipt_record_digest(receipt_path),
        "derivation": derivation,
        "case_id": case_id,
    }
    if frozen_receipt is not None:
        provenance["frozen_receipt_binding"] = frozen_receipt
    return provenance


def build_vector_document(
    parameters: Dict[str, Any],
    traces: List[Dict[str, Any]],
    provenance: Dict[str, Any],
    *,
    validate: bool,
) -> Dict[str, Any]:
    """Assemble one vector document around a generator's own content.

    Everything outside ``parameters`` / ``traces`` / ``provenance`` is the
    same in every committed vector: the schema pair, the live trace-registry
    version and parameter-inventory commit, the canonical clip block, and
    ``content_hash`` over the rest. ``validate`` additionally runs the
    document through :func:`load_vector` and
    :func:`verify_accepted_contract` at assembly time — what the control
    path generators do. The VCO generators assemble without it (their
    committed files are loader- and contract-checked by their consumers,
    ``tb/run_tb.py``'s VCO lanes and the VCO test suites), so the flag
    preserves each caller's behavior rather than changing it.
    """

    registry = load_registry_document()
    inventory = load_inventory_document()
    document: Dict[str, Any] = {
        "schema": VECTOR_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "provenance": provenance,
        "trace_registry_version": registry["semantic_version"],
        "parameter_inventory_commit": inventory["source"]["commit"],
        "clip": canonical_clip(),
        "parameters": parameters,
        "traces": traces,
    }
    document["content_hash"] = compute_content_hash(document)
    if validate:
        load_vector(document, registry=registry, inventory=inventory)
        verify_accepted_contract(document)
    return document


def check_committed(documents: Dict[str, str], out_dir: Path, tool_name: str) -> int:
    """Byte-compare a fresh in-memory regeneration against the commits.

    ``documents`` maps case id to the freshly generated JSON payload;
    ``tool_name`` names the generator in the "rerun" message.
    """

    ok = True
    for case_id, payload in sorted(documents.items()):
        path = out_dir / (case_id + ".json")
        if not path.exists():
            print("CHECK FAIL: missing %s" % path)
            ok = False
            continue
        committed = json.loads(path.read_text(encoding="utf-8"))
        fresh = json.loads(payload)
        if compute_content_hash(committed) != compute_content_hash(fresh):
            print(
                "CHECK FAIL: %s does not match regeneration; rerun "
                "tools/%s and commit" % (path, tool_name)
            )
            ok = False
        else:
            print("CHECK OK: %s" % path.name)
    return 0 if ok else 1


def generate_case_documents(
    *,
    out_dir: Path,
    directed_path: Path,
    frozen_case_id: str,
    case_table: Callable[[Dict[str, Any]], Sequence[Any]],
    build_case_document: Callable[..., Dict[str, Any]],
    frozen_receipt_document: Optional[Callable[..., Dict[str, Any]]] = None,
    documents_out: Optional[Dict[str, str]] = None,
) -> int:
    """Render every case of a control-path generator to its JSON payload.

    ``case_table(frozen_physical)`` yields ``(case_id, overrides,
    is_frozen)`` rows; ``build_case_document(formats, fcp, physical,
    case_id, frozen_receipt)`` assembles one document. With
    ``documents_out`` the payloads are collected in memory (the ``check``
    path) instead of written to ``out_dir``.
    """

    # Deferred: keeps the schema/loader module free of the model import chain.
    from .fixed_voice import AcceptedFormats
    from .format_sweep import FixedControlPath

    formats = AcceptedFormats()
    fcp = FixedControlPath(formats.control_spec)
    frozen_physical = load_frozen_physical(directed_path, frozen_case_id)
    for case_id, overrides, is_frozen in case_table(frozen_physical):
        physical = apply_overrides(frozen_physical, overrides)
        frozen_receipt = (
            frozen_receipt_document(formats, fcp, physical, case_id)
            if is_frozen and frozen_receipt_document is not None
            else None
        )
        document = build_case_document(
            formats, fcp, physical, case_id, frozen_receipt
        )
        payload = json.dumps(document, indent=1, sort_keys=True) + "\n"
        if documents_out is not None:
            documents_out[case_id] = payload
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / (case_id + ".json")
        path.write_text(payload, encoding="utf-8")
        print(
            "wrote %s (%d traces x %d values)"
            % (path, len(document["traces"]), len(document["traces"][0]["values"]))
        )
    return 0


def run_case_generator(
    argv: Optional[Sequence[str]],
    *,
    description: Optional[str],
    tool_name: str,
    out_dir: Path,
    directed_path: Path,
    frozen_case_id: str,
    case_table: Callable[[Dict[str, Any]], Sequence[Any]],
    build_case_document: Callable[..., Dict[str, Any]],
    frozen_receipt_document: Optional[Callable[..., Dict[str, Any]]] = None,
) -> int:
    """``generate`` / ``check`` entry point shared by the case generators.

    ``generate`` (the default) rewrites the committed vector files;
    ``check`` regenerates in memory and byte-compares against them. Either
    way the accepted register must instantiate first: a refusal is reported
    as ``GENERATION REFUSED`` and nothing is written.
    """

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "command",
        nargs="?",
        default="generate",
        choices=["generate", "check"],
        help="'generate' (re)writes the committed vector files; "
        "'check' byte-compares regeneration against them",
    )
    args = parser.parse_args(argv)

    # Deferred: keeps the schema/loader module free of the model import chain.
    from .fixed_voice import AcceptedFormats

    try:
        AcceptedFormats()
    except Exception as error:  # noqa: BLE001 - refusal must be explicit
        print("GENERATION REFUSED: accepted register refused: %s" % error)
        return 1

    cases = {
        "out_dir": out_dir,
        "directed_path": directed_path,
        "frozen_case_id": frozen_case_id,
        "case_table": case_table,
        "build_case_document": build_case_document,
        "frozen_receipt_document": frozen_receipt_document,
    }
    if args.command == "check":
        documents: Dict[str, str] = {}
        generate_case_documents(documents_out=documents, **cases)
        return check_committed(documents, out_dir, tool_name)
    return generate_case_documents(**cases)


def build_vco_vector(
    *,
    case_id: str,
    physical: Dict[str, Any],
    formats: Any,
    trace_name: str,
    mirror_stream_key: str,
    derive_case: Callable[[Any, Dict[str, Any]], Dict[str, Any]],
    static_words: Callable[[Dict[str, Any]], Dict[str, Any]],
    issue: int,
    generator: str,
    shadow_boundary: str,
    extra_provenance: Optional[
        Callable[[Dict[str, Any]], Dict[str, Any]]
    ] = None,
    frozen: Optional[Dict[str, Any]] = None,
    rendered_words: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Assemble one committed VCO lane vector (#535, issues #73/#74).

    The mirror-vs-frozen sidecar custody verification, the mirror-vs-model
    dedicated-render check, and the scalar ``keyboard.midi_f0`` trace tail
    are lane-independent; the lane supplies its ``derive_case``, its trace
    name (``vco_1.raw`` / ``vco_2.raw``; the provenance raw key is derived
    from it), the mirror stream key, its static-word and extra-provenance
    hooks, and its provenance labels. Assembly runs with ``validate=False``
    (each lane's committed vectors are loader- and contract-checked by
    their consumers, ``tb/run_tb.py``'s VCO lanes and the VCO test suites),
    preserving the generators' behavior.
    """

    derivation = derive_case(formats, physical)
    mirror_digest = digest_words(derivation["streams"][mirror_stream_key])
    raw_key = trace_name.replace(".", "_")

    provenance: Dict[str, Any] = {
        "issue": issue,
        "generator": generator,
        "model_identity": VCO_MODEL_IDENTITY,
        "case_id": case_id,
        "shadow_boundary": shadow_boundary,
        "dr_0008_status": VCO_DR_0008_STATUS,
        "dr_0008_constants_package_sha256": sha256_file(
            CONSTANTS_PACKAGE_PATH
        ),
        "choices_register_sha256": sha256_file(CHOICES_REGISTER_PATH),
        "static_words": static_words(derivation),
    }
    if extra_provenance is not None:
        provenance.update(extra_provenance(derivation))
    provenance[raw_key] = None
    provenance["frozen_binding"] = None

    if frozen is not None:
        if mirror_digest != frozen["digest"]:
            raise SystemExit(
                "mirror does not reproduce the frozen %s digest for "
                "%s: mirror %s frozen %s"
                % (trace_name, case_id, mirror_digest, frozen["digest"])
            )
        sidecar_payload = (ROOT / frozen["sidecar_file"]).read_bytes()
        if sha256_bytes(sidecar_payload) != frozen["sidecar_sha256"]:
            raise SystemExit(
                "frozen sidecar bytes drifted for %s (%s); regenerate"
                % (case_id, frozen["sidecar_file"])
            )
        words = unpack_words_f32le(sidecar_payload)
        if digest_words(words) != frozen["digest"]:
            raise SystemExit(
                "frozen sidecar words digest drift for %s" % case_id
            )
        row = evidence_row(words)
        if row["words_sha256"] != frozen["digest"]:
            raise SystemExit("evidence digest mismatch for " + case_id)
        provenance[raw_key] = row
        provenance["frozen_binding"] = {
            "verified": True,
            "golden_document": "sim/reference/fixed-voice-golden-v1.json",
            "case_id": frozen["golden_case_id"],
            "trace_digest": frozen["digest"],
            "sidecar": {
                "file": frozen["sidecar_file"],
                "sha256": frozen["sidecar_sha256"],
            },
        }
    else:
        if digest_words(rendered_words) != mirror_digest:
            raise SystemExit(
                "mirror does not reproduce the model's own %s render "
                "for %s" % (trace_name, case_id)
            )
        provenance[raw_key] = evidence_row(rendered_words)
        provenance["derivation"] = (
            "FixedVoiceModel.render of the declared parameters; the "
            "committed digest is the model's own %s words; the host "
            "mirror reproduced it exactly at generation time" % trace_name
        )

    traces = [
        {
            "name": "keyboard.midi_f0",
            "kind": "scalar",
            "encoding": "synthetic-int",
            "cycle_start": 0,
            "values": [derivation["words"]["keyboard.midi_f0"]],
        }
    ]
    return build_vector_document(
        dict(physical), traces, provenance, validate=False
    )


def build_vco_vectors(
    *,
    formats: Any,
    directed_path: Path,
    trace_name: str,
    frozen_sidecar_cases: Sequence[str],
    regime_cases: Iterable[Any],
    build_case_vector: Callable[..., Dict[str, Any]],
    frozen_case_extras: Optional[
        Callable[[Dict[str, Any]], Sequence[Any]]
    ] = None,
    verbose: bool = False,
    verbose_report: Optional[
        Callable[[str, Dict[str, Any]], None]
    ] = None,
    post_check: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Render one VCO lane's full vector set in memory (#535).

    ``frozen_sidecar_cases`` names the frozen receipt cases whose
    ``trace_name`` sidecars the lane validates against directly;
    ``frozen_case_extras`` (the sine lane's frozen ``regime`` row) is
    forwarded positionally before ``frozen=``. ``regime_cases`` yields
    ``(case_id, overrides, ...)`` rows whose tail, if any, is forwarded
    positionally before ``rendered_words=`` (the sine lane's directed
    ``regime``); each case renders through the frozen whole-voice model
    via :func:`render_fixed_trace`. ``post_check`` (the sine lane's
    matrix non-vacuity refusal) runs over the finished set before the
    caller's ``--check`` comparison or write.
    """

    directed = json.loads(directed_path.read_bytes())
    base_p = {
        name: entry["physical"] for name, entry in directed["base"].items()
    }
    frozen_doc = json.loads(RECEIPT_PATH.read_bytes())
    frozen_cases = {case["id"]: case for case in frozen_doc["cases"]}

    vectors: Dict[str, Any] = {}

    for case_id in frozen_sidecar_cases:
        case = frozen_cases[case_id]
        sidecar_file = (
            "sim/reference/fixed-voice-golden-v1-traces/%s.%s.f32le"
            % (case_id, trace_name)
        )
        extras = (
            frozen_case_extras(case)
            if frozen_case_extras is not None
            else ()
        )
        vectors["frozen:" + case_id] = build_case_vector(
            "frozen:" + case_id,
            case["parameters"],
            formats,
            *extras,
            frozen={
                "golden_case_id": case_id,
                "digest": case["traces"][trace_name],
                "sidecar_file": sidecar_file,
                "sidecar_sha256": sha256_file(ROOT / sidecar_file),
            },
        )
        if verbose:
            print("built frozen:%s" % case_id)

    for row in regime_cases:
        case_id, overrides = row[0], row[1]
        physical = dict(base_p)
        physical.update(overrides)
        words = render_fixed_trace(formats, physical, trace_name)
        vectors[case_id] = build_case_vector(
            case_id, physical, formats, *row[2:], rendered_words=words
        )
        if verbose:
            if verbose_report is not None:
                verbose_report(case_id, vectors[case_id])
            else:
                print("built %s" % case_id)

    if post_check is not None:
        post_check(vectors)
    return vectors


def run_vco_generator(
    argv: Optional[Sequence[str]],
    *,
    description: Optional[str],
    out_dir: Path,
    build_all: Callable[[Any, bool], Dict[str, Any]],
    tool_name: str,
    frozen_count: int,
    regime_label: str,
) -> int:
    """``--check`` / write entry point shared by the two VCO generators.

    The write path rewrites the committed vector files; ``--check``
    regenerates in memory and refuses any drift against them — including
    an orphan committed ``.json`` no generator case accounts for (#535:
    only the sine lane checked this before, so a stale square/saw file
    passed unnoticed). ``--verbose`` reports each built case (a harmless
    opt-in for the square/saw lane).
    """

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed vectors against a fresh in-memory build",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="report each built case"
    )
    args = parser.parse_args(argv)

    # Deferred: keeps the schema/loader module free of the model import chain.
    from .fixed_voice import AcceptedFormats

    try:
        formats = AcceptedFormats()
    except Exception as error:  # noqa: BLE001 - refusal must be explicit
        print("REFUSED: accepted register refused: %s" % error)
        return 1

    vectors = build_all(formats, args.verbose)

    if args.check:
        mismatches = []
        for case_id, vector in sorted(vectors.items()):
            path = out_dir / (case_id + ".json")
            if not path.exists():
                mismatches.append((case_id, "missing committed file"))
                continue
            committed = load_vector(path)
            if committed != vector:
                mismatches.append((case_id, "regeneration differs"))
        extra = sorted(
            path.stem for path in out_dir.glob("*.json")
            if path.stem not in vectors
        )
        for case_id in extra:
            mismatches.append(
                (case_id, "committed file has no generator case")
            )
        if mismatches:
            for case_id, why in mismatches:
                print("CHECK FAIL: %s: %s" % (case_id, why))
            return 1
        print(
            "CHECK OK: %d committed %s vectors regenerate exactly "
            "(deterministic)" % (len(vectors), tool_name)
        )
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for case_id, vector in sorted(vectors.items()):
        path = out_dir / (case_id + ".json")
        path.write_text(
            json.dumps(vector, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            shown = path.relative_to(ROOT)
        except ValueError:  # out_dir outside the repo (unit tests)
            shown = path
        print("wrote %s" % shown)
    print(
        "wrote %d vectors (%d frozen-binding, %d %s regime)"
        % (
            len(vectors),
            frozen_count,
            len(vectors) - frozen_count,
            regime_label,
        )
    )
    return 0


def render_fixed_trace(formats: Any, physical: Dict[str, Any], trace_name: str) -> List[int]:
    """Render one case through the frozen whole-voice fixed model.

    The normalized entries are placeholders: the fixed model consumes the
    physical map only (the normalized map is the float-side seam), and the
    committed evidence is the fixed model's own ``trace_name`` words.
    """

    # Deferred: keeps the schema/loader module free of the model import chain.
    from . import float_interfaces as fi
    from . import float_sources as fs
    from .fixed_voice import FixedVoiceModel

    noise_bytes = fs.NoiseSource.resolve(0)
    request = fi.ResolvedRequest(
        0,
        {name: 0.0 for name in physical},
        {
            "seed": 13,
            "slot": 0,
            "sample_count": 176400,
            "sha256": hashlib.sha256(noise_bytes).hexdigest(),
            "samples": noise_bytes,
        },
        physical=dict(physical),
        execution_status="canonical-batched",
    )
    model = FixedVoiceModel(request, formats)
    fixed, _diagnostics = model.render()
    return fixed[trace_name]


def evidence_row(words: List[int]) -> Dict[str, Any]:
    """The committed evidence row for one raw word stream."""

    pins = {0: words[0], 1: words[1], 2: words[2]}
    for j in (99, 441, 1764, 17640, 88200, 105840, 176397, 176398, 176399):
        pins[j] = words[j]
    return {
        "sample_count": len(words),
        "words_sha256": digest_words(words),
        "first_word": words[0],
        "last_word": words[-1],
        "jitter": {str(j): w for j, w in sorted(pins.items())},
    }


def pack_words_f32le(words: List[int], frac_bits: int = 21) -> bytes:
    """Pack Q-format words as little-endian binary32 (the #54 convention).

    ``word / 2**frac_bits`` is exactly representable whenever the signed
    word fits in ``frac_bits + 23`` bits, so the packing is lossless; the
    unpacker refuses any drift anyway.
    """

    scale = float(1 << frac_bits)
    return struct.pack("<%df" % len(words), *[word / scale for word in words])


def unpack_words_f32le(payload: bytes, frac_bits: int = 21) -> List[int]:
    """Unpack binary32 payload back to exact words, refusing any drift."""

    scale = 1 << frac_bits
    values = struct.unpack("<%df" % (len(payload) // 4), payload)
    words: List[int] = []
    for value in values:
        scaled = value * scale
        rounded = int(round(scaled))
        if scaled != rounded:
            raise VectorError(
                "f32le payload value %r is not an exact Q%d word multiple"
                % (value, frac_bits)
            )
        words.append(rounded)
    return words


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
