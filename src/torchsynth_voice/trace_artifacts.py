"""Companion trace bundles binding captured Voice traces to published artifacts.

The sole integration layer between #23's validated passive capture records and
the landed v1 artifact/store/corpus producers (#14/#15/#105): it binds the
content-qualified trace-registry identity into render requests, validates the
capture descriptors against the pinned registry and the exact published
artifact bytes, and maintains the companion corpus linkage. Bundles and
companions live strictly outside completed artifact directories; v1 artifact
trace references stay closed ``{ref, sha256, size_bytes}`` objects.

All verification is stdlib-only and read-only: no Torch, TorchSynth, NumPy or
renderer import is required, and no store byte is ever repaired or rewritten.
See spec/TRACE-ARTIFACTS.md.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import trace_registry
from .artifact_renderer import digest, json_bytes, require
from .artifacts import (
    ValidationError,
    _structure,
    loads,
    validate_corpus_index,
    verify_sha256,
)
from .contract import repository_root
from .corpus import read_bytes, read_reference, write_once
from .storage import ArtifactStore

BUNDLE_SCHEMA = "torchsynth-trace-bundle"
COMPANION_SCHEMA = "torchsynth-trace-companion-index"
SCHEMA_VERSION = 1
SCHEMA_FILENAME = "trace-bundle-v1.schema.json"
SCHEMA_PATH = repository_root() / "spec/schemas" / SCHEMA_FILENAME
REGISTRY_REF = "spec/reference/trace-registry-v1.json"
ENCODING = "f32le"
DTYPE = "float32"
DTYPE_WIDTH = 4
STORAGE = {"compression": "none"}
# The landed release worker owns the single profiler slot during the render;
# these seams are observed by that boundary profiler, not double-captured.
NORMALIZATION_SEAMS = ("mixer.pre_normalization", "mixer.peak", "mixer.gain")


def _schema_option(name):
    document = loads(SCHEMA_PATH.read_bytes())
    for option in document["oneOf"]:
        if option["properties"]["schema"]["const"] == name:
            return option
    raise ValidationError("companion schema is missing the " + name + " form")


def _structure_option(value, name):
    _structure(value, _schema_option(name), SCHEMA_FILENAME)


def registry_binding():
    """Content-qualified identity of the exact pinned registry bytes."""
    raw = trace_registry.REGISTRY_PATH.read_bytes()
    trace_registry.validate_registry(trace_registry.loads(raw))
    return dict(
        token=trace_registry.registry_token(),
        sha256=hashlib.sha256(raw).hexdigest(),
        ref=REGISTRY_REF,
    )


def traced_request_fields(document=None, names=None):
    """Registry-bound request fields; the order is the approved graph order."""
    document = (
        trace_registry.load_registry() if document is None else document
    )
    return dict(
        trace_registry_version=registry_binding()["token"],
        requested_traces=trace_registry.requested_names(document, names),
    )


def production_selection(document=None):
    """Graph-ordered worker-compatible selection for the release path.

    Every registry trace except the three normalization seams: the landed
    release worker owns the process's single profiler slot while its original
    normalization boundary is observed, so those seams are recorded by the
    worker receipt (pre-normalization digest) instead of a second observer.
    """
    document = (
        trace_registry.load_registry() if document is None else document
    )
    ordered = trace_registry.requested_names(document, None)
    return [name for name in ordered if name not in NORMALIZATION_SEAMS]


def element_count(shape):
    require(type(shape) is list and all(type(n) is int and n >= 0 for n in shape),
            "shape must be a list of non-negative integers")
    count = 1
    for length in shape:
        count *= length
    return count


def _descriptor(document, item):
    expected = {trace["name"]: trace for trace in document["traces"]}[item["name"]]
    classification = expected["kind"]
    return dict(
        file=None,  # filled by the caller from the closed artifact reference
        encoding=ENCODING,
        dtype=item["dtype"],
        shape=list(item["shape"]),
        classification=classification,
        rate_hz=expected["rate_hz"],
        sample_count=expected["sample_count"],
        boundary=expected["boundary"],
        observation=expected["observation"],
        capture_sha256=item["sha256"],
    )


def build_bundle(stored, record, inventory):
    """Bind validated capture descriptors to one published traced artifact.

    ``stored`` is the ``StoredArtifact`` returned by ``ArtifactStore.publish``
    or ``verify``; ``record`` is its parsed metadata; ``inventory`` is the
    graph-ordered capture inventory recorded by a #23 ``TraceCapture`` session.
    """
    require(record["status"] == "complete", "bundles require a complete artifact")
    require(
        record["traces"]["state"] == "available",
        "bundles require available traces",
    )
    document = trace_registry.load_registry()
    binding = registry_binding()
    inputs = record["inputs"]["value"]
    require(
        inputs["trace_registry_version"] == binding["token"],
        "artifact was not rendered under the pinned registry token",
    )
    names = trace_registry.requested_names(document, inputs["requested_traces"])
    require(type(inventory) is list and inventory, "capture inventory required")
    require(
        [item["name"] for item in inventory] == names,
        "capture inventory is not the graph-ordered requested set",
    )
    published = record["traces"]["value"]
    require(
        set(published) == set(names),
        "published trace names differ from the requested set",
    )
    traces = {}
    for item in inventory:
        name = item["name"]
        require(
            set(published[name]) == {"ref", "sha256", "size_bytes"},
            "v1 trace file references must stay closed objects: " + name,
        )
        descriptor = _descriptor(document, item)
        descriptor["file"] = dict(published[name])
        width = element_count(descriptor["shape"]) * DTYPE_WIDTH
        require(
            descriptor["dtype"] == DTYPE,
            "bundle v1 preserves the original binary32 encoding: " + name,
        )
        require(
            published[name]["size_bytes"] == width,
            "stored payload width disagrees with the declared shape: " + name,
        )
        if descriptor["classification"] == "scalar":
            require(
                descriptor["rate_hz"] is None
                and descriptor["sample_count"] is None,
                "scalar facts carry no invented rate or count: " + name,
            )
        else:
            require(
                descriptor["rate_hz"] in (441, 44100)
                and descriptor["sample_count"]
                == element_count(descriptor["shape"]),
                "sampled descriptor disagrees with the pinned registry: " + name,
            )
        require(
            descriptor["capture_sha256"] == published[name]["sha256"],
            "captured bytes are not the published payload: " + name,
        )
        traces[name] = descriptor
    return dict(
        schema=BUNDLE_SCHEMA,
        schema_version=SCHEMA_VERSION,
        status="complete",
        artifact=dict(stored.reference),
        registry=binding,
        fixture=dict(inputs["fixture"]),
        requested_traces=list(names),
        traces=traces,
        storage=dict(STORAGE),
        warnings=[],
        failures=[],
    )


def bundle_reference(bundle):
    """Portable store-root-relative reference to the exact published bytes."""
    data = json_bytes(bundle)
    return dict(
        ref="trace-bundles/" + bundle["artifact"]["artifact_id"] + ".json",
        sha256=digest(data),
        size_bytes=len(data),
    )


def validate_bundle_document(bundle, *, binding=None):
    """Structure, registry identity and registry-agreement checks alone.

    Verifies everything that does not require the store: schema shape, the
    content-qualified registry binding, graph-ordered requested subset, and
    per-name descriptor agreement with the pinned registry. Store and payload
    verification live in validate_bundle.
    """
    require(type(bundle) is dict, "bundle must be an object")
    _structure_option(bundle, BUNDLE_SCHEMA)
    binding = registry_binding() if binding is None else binding
    require(
        bundle["registry"] == binding,
        "bundle registry identity is stale or foreign",
    )
    document = trace_registry.load_registry()
    names = trace_registry.requested_names(document, bundle["requested_traces"])
    require(
        set(bundle["traces"]) == set(names),
        "bundle trace names differ from the requested registry subset",
    )
    by_name = {t["name"]: t for t in document["traces"]}
    for name, descriptor in bundle["traces"].items():
        expected = by_name[name]
        require(
            set(descriptor) == {
                "file",
                "encoding",
                "dtype",
                "shape",
                "classification",
                "rate_hz",
                "sample_count",
                "boundary",
                "observation",
                "capture_sha256",
            },
            "bundle descriptor fields mismatch: " + name,
        )
        require(
            descriptor["classification"] == expected["kind"]
            and descriptor["rate_hz"] == expected["rate_hz"]
            and descriptor["sample_count"] == expected["sample_count"]
            and descriptor["boundary"] == expected["boundary"]
            and descriptor["observation"] == expected["observation"]
            and descriptor["shape"] == expected["shape"],
            "descriptor disagrees with the pinned registry: " + name,
        )
        require(
            descriptor["encoding"] == ENCODING and descriptor["dtype"] == DTYPE,
            "descriptor encoding is not the original binary32: " + name,
        )
        require(
            descriptor["file"]["size_bytes"]
            == element_count(descriptor["shape"]) * DTYPE_WIDTH,
            "declared payload width disagrees with the shape: " + name,
        )
        require(
            set(descriptor["file"]) == {"ref", "sha256", "size_bytes"},
            "v1 trace file references must stay closed objects: " + name,
        )
        require(
            descriptor["capture_sha256"] == descriptor["file"]["sha256"],
            "capture digest is not the published payload digest: " + name,
        )
    return True


def validate_bundle(bundle, *, store: ArtifactStore):
    """Full verification: structure, registry identity, artifact and payloads.

    Rehashes every declared trace payload byte through the landed store
    verifier and returns the rehashed-byte accounting.
    """
    validate_bundle_document(bundle)
    artifact = bundle["artifact"]
    stored = store.verify(artifact["artifact_id"], sha256=artifact["sha256"])
    metadata = read_bytes(stored.path / "metadata.json")
    record = loads(metadata)
    verify_sha256(metadata, artifact["sha256"])
    inputs = record["inputs"]["value"]
    require(
        inputs["trace_registry_version"] == registry_binding()["token"],
        "artifact registry identity is stale",
    )
    require(
        inputs["requested_traces"] == bundle["requested_traces"],
        "bundle requests differ from the artifact inputs",
    )
    require(
        bundle["fixture"] == inputs["fixture"],
        "bundle fixture disagrees with the artifact",
    )
    published = record["traces"]["value"]
    stats = dict(
        artifact_bytes=len(metadata), trace_payloads=0, trace_bytes=0
    )
    for name, descriptor in bundle["traces"].items():
        require(
            descriptor["file"] == published[name],
            "bundle file reference is not the exact artifact reference: " + name,
        )
        payload = read_reference(stored.path, descriptor["file"])
        require(
            len(payload)
            == element_count(descriptor["shape"]) * DTYPE_WIDTH,
            "payload width disagrees with the declared shape: " + name,
        )
        stats["trace_payloads"] += 1
        stats["trace_bytes"] += len(payload)
    stats["bundle_bytes"] = len(json_bytes(bundle))
    return stats


def build_companion(*, index_reference, index, bundles, variant_index_reference=None):
    """Link one traced v1 corpus index to its per-case bundles.

    ``index_reference`` pins the exact validated v1 index bytes; ``bundles``
    are built bundle documents for every complete case. Failed cases stay
    listed with null bundles: no silent omission.
    """
    binding = registry_binding()
    require(
        type(index) is dict and index.get("schema") == "torchsynth-corpus-index",
        "companion requires a v1 corpus index document",
    )
    by_artifact = {}
    for bundle in bundles:
        require(bundle["schema"] == BUNDLE_SCHEMA, "unexpected bundle document")
        by_artifact[bundle["artifact"]["artifact_id"]] = bundle
    cases = []
    for case in index["cases"]:
        complete = case["status"] == "complete"
        artifact = dict(case["artifact"]) if complete else None
        bundle = None
        if complete:
            require(
                artifact["artifact_id"] in by_artifact,
                "complete case has no bundle: " + case["case_id"],
            )
            bundle = bundle_reference(by_artifact.pop(artifact["artifact_id"]))
        cases.append(
            dict(
                case_id=case["case_id"],
                sound_index=case["fixture"]["sound_index"],
                fixture=dict(case["fixture"]),
                status=case["status"],
                artifact=artifact,
                bundle=bundle,
                warnings=list(case["warnings"]),
                failures=list(case["failures"]),
            )
        )
    require(not by_artifact, "bundles for cases outside the index")
    return dict(
        schema=COMPANION_SCHEMA,
        schema_version=SCHEMA_VERSION,
        status=index["status"],
        registry=binding,
        index=dict(index_reference),
        variant_index=None
        if variant_index_reference is None
        else dict(variant_index_reference),
        cases=cases,
        storage=dict(STORAGE),
        warnings=[],
        failures=[],
    )


def companion_reference(companion):
    data = json_bytes(companion)
    return dict(
        ref="trace-companions/" + digest(data) + ".json",
        sha256=digest(data),
        size_bytes=len(data),
    )


def _load_index(root, file_reference, *, store):
    data = read_reference(root, file_reference)
    index = loads(data)
    artifacts = {}
    for case in index["cases"]:
        if case["status"] != "complete":
            continue
        stored = store.verify(
            case["artifact"]["artifact_id"], sha256=case["artifact"]["sha256"]
        )
        artifacts[stored.artifact_id] = loads(
            read_bytes(stored.path / "metadata.json")
        )
    validate_corpus_index(index, require_complete=False, artifacts=artifacts)
    return index, artifacts


def validate_companion_document(companion, *, binding=None):
    """Structure and registry-identity checks that need no store access."""
    require(type(companion) is dict, "companion must be an object")
    _structure_option(companion, COMPANION_SCHEMA)
    binding = registry_binding() if binding is None else binding
    require(
        companion["registry"] == binding,
        "companion registry identity is stale or foreign",
    )
    return True


def validate_companion(companion, *, root, store: ArtifactStore):
    """Full verification of the companion linkage and everything it pins.

    Revalidates both pinned v1 indexes through the landed corpus validator,
    every referenced bundle, and every trace/audio payload byte; returns the
    rehashed-byte accounting.
    """
    require(type(companion) is dict, "companion must be an object")
    _structure_option(companion, COMPANION_SCHEMA)
    binding = registry_binding()
    require(
        companion["registry"] == binding,
        "companion registry identity is stale or foreign",
    )
    index, artifacts = _load_index(root, companion["index"], store=store)
    require(
        companion["status"] == index["status"],
        "companion status disagrees with the pinned index",
    )
    cases = index["cases"]
    require(
        len(companion["cases"]) == len(cases),
        "companion coverage omits or adds cases",
    )
    stats = dict(cases=len(cases), bundles=0, bundle_bytes=0)
    for linked, case in zip(companion["cases"], cases):
        require(
            linked["case_id"] == case["case_id"]
            and linked["sound_index"] == case["fixture"]["sound_index"]
            and linked["fixture"] == case["fixture"]
            and linked["status"] == case["status"]
            and linked["artifact"] == case["artifact"],
            "companion case diverges from the index: " + linked["case_id"],
        )
        complete = case["status"] == "complete"
        require(
            complete == (linked["bundle"] is not None),
            "bundle presence must follow case completion: " + linked["case_id"],
        )
        if not complete:
            continue
        data = read_reference(root, linked["bundle"])
        bundle = loads(data)
        require(
            json_bytes(bundle) == data,
            "bundle bytes are not the canonical serialization",
        )
        require(
            bundle["artifact"]["artifact_id"] == case["artifact"]["artifact_id"],
            "bundle points at a different artifact: " + linked["case_id"],
        )
        validated = validate_bundle(bundle, store=store)
        stats["bundles"] += 1
        stats["bundle_bytes"] += validated["bundle_bytes"]
        stats["trace_payloads"] = stats.get("trace_payloads", 0) + validated["trace_payloads"]
        stats["trace_bytes"] = stats.get("trace_bytes", 0) + validated["trace_bytes"]
    if companion["variant_index"] is not None:
        stats.update(
            _validate_variant(companion, root=root, store=store, index=index, artifacts=artifacts)
        )
    return stats


def _validate_variant(companion, *, root, store, index, artifacts):
    variant_index, variant_artifacts = _load_index(
        root, companion["variant_index"], store=store
    )
    require(
        variant_index["corpus_manifest_sha256"]
        == index["corpus_manifest_sha256"],
        "variant indexes draw from different manifests",
    )
    require(
        variant_index["corpus_id"] != index["corpus_id"],
        "variant indexes must be distinct renders",
    )
    variant_by_index = {
        case["fixture"]["sound_index"]: case for case in variant_index["cases"]
    }
    audio_bytes = 0
    for case in index["cases"]:
        if case["status"] != "complete":
            continue
        other = variant_by_index.get(case["fixture"]["sound_index"])
        require(
            other is not None and other["status"] == "complete",
            "variant index lacks the same sound identity: " + case["case_id"],
        )
        require(
            other["fixture"] == case["fixture"],
            "variant fixture disagrees for the same sound identity: "
            + case["case_id"],
        )
        traced = artifacts[case["artifact"]["artifact_id"]]
        plain = variant_artifacts[other["artifact"]["artifact_id"]]
        require(
            traced["inputs"]["value"]["fixture"] == plain["inputs"]["value"]["fixture"]
            and traced["inputs"]["value"]["profile"]
            == plain["inputs"]["value"]["profile"]
            and traced["inputs"]["value"]["runtime"]
            == plain["inputs"]["value"]["runtime"]
            and traced["inputs"]["value"]["execution"]
            == plain["inputs"]["value"]["execution"]
            and traced["inputs"]["value"]["parameters"]
            == plain["inputs"]["value"]["parameters"]
            and traced["inputs"]["value"]["noise"] == plain["inputs"]["value"]["noise"]
            and traced["inputs"]["value"]["source"] == plain["inputs"]["value"]["source"],
            "variants of one sound identity disagree beyond the declared trace fields",
        )
        differ = set()
        for field in ("trace_registry_version", "requested_traces"):
            if traced["inputs"]["value"][field] != plain["inputs"]["value"][field]:
                differ.add(field)
        require(
            differ == {"trace_registry_version", "requested_traces"},
            "variants must differ exactly in the declared trace fields",
        )
        traced_audio = _artifact_audio(store, traced)
        plain_audio = _artifact_audio(store, plain)
        require(
            traced_audio == plain_audio,
            "traced and audio-only audio bytes differ for one sound identity",
        )
        audio_bytes += len(traced_audio)
    return dict(audio_bytes_rehashed=audio_bytes)


def _artifact_audio(store, record):
    reference = record["audio"]["value"]["file"]
    data = read_bytes(
        store.root / "artifacts" / record["artifact_id"] / reference["ref"]
    )
    verify_sha256(data, reference["sha256"])
    require(
        len(data) == reference["size_bytes"], "audio byte count mismatch"
    )
    return data


def publish_companion_surface(root, bundles, companion):
    """Publish bundles first, the companion last; never replace prior bytes."""
    for bundle in bundles:
        write_once(Path(root) / bundle_reference(bundle)["ref"], json_bytes(bundle))
    write_once(
        Path(root) / companion_reference(companion)["ref"], json_bytes(companion)
    )


def storage_cost(root, companion):
    """Measured per-case and aggregate companion-surface bytes.

    Trace payload sizes are the exact declared artifact references; audio
    payloads are the contract-fixed clip size and are accounted by the caller
    from the store.
    """
    root = Path(root)
    per_case = {}
    for linked in companion["cases"]:
        if linked["bundle"] is None:
            continue
        data = read_reference(root, linked["bundle"])
        bundle = loads(data)
        per_case[linked["case_id"]] = dict(
            trace_payload_bytes=sum(
                bundle["traces"][name]["file"]["size_bytes"]
                for name in bundle["traces"]
            ),
            bundle_bytes=len(data),
        )
    aggregate = dict(
        bundle_count=len(per_case),
        bundle_bytes=sum(case["bundle_bytes"] for case in per_case.values()),
        companion_bytes=len(json_bytes(companion)),
        trace_payload_bytes=sum(
            case["trace_payload_bytes"] for case in per_case.values()
        ),
    )
    return dict(per_case=per_case, aggregate=aggregate)
