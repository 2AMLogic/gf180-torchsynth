#!/usr/bin/env python3
"""Qualify traced render artifacts; validate inputs, or run the bounded smoke.

Default mode (DR-0006 host only) runs the actual two-development-case traced
corpus through the landed #15 renderer/corpus APIs with #23's passive capture,
publishes the companion bundle surface outside the artifact directories,
resumes both runs with zero renderer/capture calls while rehashing every
referenced byte, and records the bounded publication. ``--check-inputs`` and
``--check-publication`` are stdlib-only and run on ordinary hosts.

This tool owns the #24 process integration: the stock landed Docker backend
accepts audio-only requests only, so the traced launch is implemented here and
attaches the container-side capture provider built on ``trace_capture``.
"""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import trace_artifacts as ta  # noqa: E402
from torchsynth_voice import trace_registry  # noqa: E402
from torchsynth_voice.artifact_renderer import (  # noqa: E402
    DockerBackend,
    PROFILE,
    THREAD_ENV,
    digest,
    json_bytes,
    loads,
    project_identity,
    qualification,
    render_artifact,
    request_template,
    sha256_file,
    validate_request,
    require,
)
from torchsynth_voice.artifacts import (  # noqa: E402
    ValidationError,
    canonical_bytes,
    validate_corpus_index,
)
from torchsynth_voice.contract import repository_root  # noqa: E402
from torchsynth_voice.corpus import (  # noqa: E402
    read_reference,
    run_corpus,
    select_cases,
    write_once,
)
from torchsynth_voice.storage import ArtifactStore  # noqa: E402

RECEIPT_PATH = ROOT / "sim/reference/trace-artifact-smoke.json"
INDICES = [0, 1]

# Python 3.9 driver executed inside the qualified image. It imports the landed
# worker and the #23 capture modules directly from the read-only repository
# mount, attaches a TraceCapture provider around the single Voice call, and
# writes the selected-sound trace bytes plus their validated descriptors.
DRIVER_SOURCE = '''\
"""Container-side #24 traced provider; Python 3.9, offline, writes /output."""
import hashlib
import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, "/repo/env/release-era")
sys.path.insert(0, "/repo/src/torchsynth_voice")

import render_artifact as worker
import trace_capture
import trace_registry
from torchsynth_voice.trace_artifacts import NORMALIZATION_SEAMS


class ProviderSession(object):
    def __init__(self, factory, session, torch):
        self.factory = factory
        self.session = session
        self.torch = torch

    def __enter__(self):
        self.session.__enter__()
        return self.factory.payloads

    def __exit__(self, exc_type, exc, tb):
        result = self.session.__exit__(exc_type, exc, tb)
        if exc_type is None:
            for item in self.session.inventory:
                name = item["name"]
                data = trace_capture.tensor_bytes(
                    self.session.values[name], self.torch
                )
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ValueError(
                        "serialized bytes disagree with capture: " + name
                    )
                self.factory.descriptors[name] = item
                self.factory.payloads[name] = data
        return result


class ProviderFactory(object):
    def __init__(self, document, requested, batch_size):
        self.document = document
        self.requested = requested
        self.batch_size = batch_size
        self.payloads = {}
        self.descriptors = {}

    def __call__(self, request, voice, slot):
        import torch
        from torchsynth.util import normalize_if_clipping

        session = trace_capture.TraceCapture(
            voice,
            self.document,
            torch,
            normalize_if_clipping,
            names=self.requested,
            slot=slot,
            batch_size=self.batch_size,
        )
        return ProviderSession(self, session, torch)


def main():
    output = Path("/output")
    request_bytes = (output / "request.json").read_bytes()
    request = json.loads(request_bytes)
    document = trace_registry.load_registry()
    if request["trace_registry_version"] != trace_registry.registry_token():
        raise ValueError("request does not carry the pinned registry token")
    if not request["requested_traces"]:
        raise ValueError("traced driver requires requested traces")
    requested = trace_registry.requested_names(
        document, request["requested_traces"]
    )
    if set(requested) & set(NORMALIZATION_SEAMS):
        raise ValueError(
            "normalization seams are worker-observed, not re-captured"
        )
    factory = ProviderFactory(
        document, requested, request["execution"]["batch_size"]
    )
    with warnings.catch_warnings(record=True) as seen:
        observation, payloads = worker.render_selected(
            request, Path("/opt/torchsynth"), capture_provider=factory
        )
    receipt = observation["receipt"]
    receipt["warning_categories"] = sorted({w.category.__name__ for w in seen})
    receipt["request_sha256"] = hashlib.sha256(request_bytes).hexdigest()
    observation.pop("traces")
    directory = output / "traces"
    directory.mkdir()
    descriptors = []
    for index, name in enumerate(requested):
        data = factory.payloads[name]
        descriptor = dict(factory.descriptors[name])
        descriptor["file_ref"] = "traces/trace-%d.bin" % index
        (output / descriptor["file_ref"]).write_bytes(data)
        descriptors.append(descriptor)
    (output / "trace-descriptors.json").write_text(
        json.dumps(descriptors, sort_keys=True, indent=2, allow_nan=False)
        + "\\n"
    )
    (output / "observation.json").write_text(
        json.dumps(observation, sort_keys=True, indent=2, allow_nan=False)
        + "\\n"
    )


if __name__ == "__main__":
    main()
'''


def registry_binding():
    binding = ta.registry_binding()
    require(
        binding["token"] == trace_registry.registry_token(),
        "registry token recomputation disagrees",
    )
    return binding


def selection_record():
    document = trace_registry.load_registry()
    selection = ta.production_selection(document)
    excluded = [
        t["name"] for t in document["traces"] if t["name"] in ta.NORMALIZATION_SEAMS
    ]
    return dict(
        requested_traces=selection,
        excluded_normalization_seams=excluded,
        selection_sha256=digest(canonical_bytes(selection)),
    )


def check_inputs():
    """Stdlib-only preregistered pins and structural negative controls."""
    binding = registry_binding()
    selection = selection_record()
    document = trace_registry.load_registry()
    fields = ta.traced_request_fields(document, selection["requested_traces"])
    require(
        fields["trace_registry_version"] == binding["token"],
        "request fields do not carry the registry token",
    )
    schema = loads(ta.SCHEMA_PATH.read_bytes())
    forms = [
        option["properties"]["schema"]["const"] for option in schema["oneOf"]
    ]
    require(
        forms == [ta.BUNDLE_SCHEMA, ta.COMPANION_SCHEMA],
        "companion schema forms changed",
    )
    for name in selection["requested_traces"]:
        require(name not in ta.NORMALIZATION_SEAMS, "seam leaked into selection")
    manifest = ROOT / "spec/reference/corpus-v0.json"
    cases = select_cases(loads(manifest.read_bytes()), indices=INDICES)
    require(
        [case["sound_index"] for case in cases] == INDICES,
        "preregistered development identities changed",
    )
    try:
        select_cases(loads(manifest.read_bytes()), indices=[96])
    except ValidationError:
        pass
    else:
        raise ValidationError("holdout identity was not refused")
    controls = trace_registry.negative_controls(document)
    require(
        all(control["status"] == "rejected" for control in controls.values()),
        "registry negative controls did not all reject",
    )
    return dict(
        registry=binding,
        selection=selection,
        cases=[case["sound_index"] for case in cases],
        negative_controls=sorted(controls),
    )


def host_admission(publication):
    """DR-0006 host and image gates, identical in scope to the landed backend."""
    image = publication["provenance"]["image"]["Id"]
    host = dict(
        host_platform=platform.platform(),
        host_os_version=platform.mac_ver()[0],
        host_architecture=platform.machine(),
        host_cpu=subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip(),
        docker_server=subprocess.check_output(
            [
                "docker",
                "version",
                "--format",
                "{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}",
            ],
            text=True,
        ).strip(),
    )
    require(
        platform.system() == "Darwin"
        and host["host_os_version"] == "26.5.1"
        and host["host_architecture"] == "arm64"
        and host["host_cpu"] == "Apple M5"
        and host["docker_server"] == publication["provenance"]["docker_server"],
        "host outside DR-0006 measured scope",
    )
    inspection = loads(
        subprocess.check_output(["docker", "image", "inspect", image])
    )[0]
    require(
        inspection["Id"] == image
        and inspection["Architecture"] == "amd64"
        and inspection["Os"] == "linux",
        "qualified image mismatch",
    )
    return host, image


class TracedDockerBackend:
    """Qualified launch whose worker attaches the #23 capture provider.

    The landed stock backend refuses providers; this #24-owned integration
    reuses its admission checks and launch environment and adds the
    container-side driver that binds ``TraceCapture`` to the same worker call.
    """

    def __init__(self, *, project_root=None):
        self.project_root = Path(project_root or repository_root()).resolve()

    def __call__(self, request, *, capture_provider=None):
        validate_request(request)
        require(
            capture_provider is None,
            "host-side providers cannot cross the worker process boundary",
        )
        require(
            request["requested_traces"],
            "use the stock DockerBackend for audio-only requests",
        )
        require(
            not request["project_git"]["dirty"],
            "production requires a frozen clean producer checkout",
        )
        require(
            project_identity(self.project_root) == request["project_git"],
            "producer checkout changed",
        )
        publication, expected_runtime = qualification()
        host, image = host_admission(publication)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            (directory / "request.json").write_bytes(json_bytes(request))
            (directory / "driver.py").write_text(DRIVER_SOURCE)
            command = [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--platform",
                "linux/amd64",
                "--network",
                "none",
                "--memory",
                "6g",
                "--cpus",
                "1",
                *[
                    part
                    for k, v in THREAD_ENV.items()
                    for part in ("--env", k + "=" + v)
                ],
                "--env",
                "MKL_CBWR=COMPATIBLE",
                "--mount",
                "type=bind,src=" + str(self.project_root) + ",dst=/repo,readonly",
                "--mount",
                "type=bind,src=" + str(directory) + ",dst=/output",
                "--entrypoint",
                "env",
                image,
                "-u",
                "ATEN_CPU_CAPABILITY",
                "python",
                "/output/driver.py",
            ]
            result = subprocess.run(
                command, capture_output=True, timeout=900, check=False
            )
            require(
                result.returncode == 0,
                "traced worker failed: "
                + result.stderr.decode(errors="replace")[-2000:],
            )
            observed = loads((directory / "observation.json").read_bytes())
            for name in ("audio", "noise", "pre_normalization"):
                observed[name] = (directory / (name + ".f32le")).read_bytes()
            traces = {}
            for descriptor in loads(
                (directory / "trace-descriptors.json").read_bytes()
            ):
                data = (directory / descriptor["file_ref"]).read_bytes()
                require(
                    hashlib.sha256(data).hexdigest() == descriptor["sha256"],
                    "returned trace bytes disagree with the capture descriptor: "
                    + descriptor["name"],
                )
                traces[descriptor["name"]] = data
            require(
                set(traces) == set(request["requested_traces"]),
                "traced worker returned the wrong trace set",
            )
            observed["traces"] = traces
            receipt = observed["receipt"]
            require(
                receipt["request_sha256"] == digest(json_bytes(request)),
                "worker request mismatch",
            )
            require(
                receipt["worker_sha256"]
                == sha256_file(
                    self.project_root / "env/release-era/render_artifact.py"
                ),
                "worker implementation mismatch",
            )
            require(
                receipt["runtime"] == expected_runtime,
                "actual worker outside qualified identity",
            )
            receipt.update(
                runtime_profile=PROFILE,
                host=host,
                image=image,
                command=command,
                exit_code=result.returncode,
                stdout_sha256=digest(result.stdout),
                stderr_sha256=digest(result.stderr),
            )
        require(
            project_identity(self.project_root) == request["project_git"],
            "producer changed during render",
        )
        return observed


def build_case_bundle(store, case):
    """Verify one stored traced case through the landed store and build its bundle."""
    stored = store.verify(
        case["artifact"]["artifact_id"], sha256=case["artifact"]["sha256"]
    )
    record = loads((stored.path / "metadata.json").read_bytes())
    document = trace_registry.load_registry()
    by_name = {t["name"]: t for t in document["traces"]}
    names = record["inputs"]["value"]["requested_traces"]
    order = trace_registry.requested_names(document, names)
    inventory = []
    for name in order:
        reference = record["traces"]["value"][name]
        data = (stored.path / reference["ref"]).read_bytes()
        expected = by_name[name]
        inventory.append(
            dict(
                name=name,
                shape=list(expected["shape"]),
                batch_shape=[
                    32 if n == "B" else n for n in expected["batch_shape"]
                ],
                dtype="float32",
                rate_hz=expected["rate_hz"],
                boundary=expected["boundary"],
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    return ta.build_bundle(stored, record, inventory)


def run_smoke(store_root, receipt_path):
    """The bounded executed evidence run: two development identities."""
    identity = project_identity(repository_root())
    require(
        not identity["dirty"],
        "smoke requires a clean committed producer checkout",
    )
    publication, _ = qualification()
    host, image = host_admission(publication)
    store_root = Path(store_root).resolve()
    require(
        not store_root.exists(), "smoke store root must not already exist"
    )
    store = ArtifactStore(store_root)
    manifest = ROOT / "spec/reference/corpus-v0.json"
    binding = registry_binding()
    selection = selection_record()

    renders = {"plain": 0, "traced": 0}

    def plain_renderer(request, store_):
        renders["plain"] += 1
        return render_artifact(request, store_, DockerBackend())

    def traced_renderer(request, store_):
        renders["traced"] += 1
        return render_artifact(request, store_, TracedDockerBackend())

    plain = run_corpus(
        store_root,
        manifest,
        request_template(),
        plain_renderer,
        indices=INDICES,
    )
    traced_template = request_template(
        trace_registry_version=binding["token"],
        requested_traces=selection["requested_traces"],
    )
    traced = run_corpus(
        store_root,
        manifest,
        traced_template,
        traced_renderer,
        indices=INDICES,
    )
    require(renders == {"plain": 2, "traced": 2}, "unexpected render count")

    traced_index = loads(read_reference(store_root, traced["index"]))
    bundles = []
    for case in traced_index["cases"]:
        require(case["status"] == "complete", "smoke case failed")
        bundles.append(build_case_bundle(store, case))
    companion = ta.build_companion(
        index_reference=traced["index"],
        index=traced_index,
        bundles=bundles,
        variant_index_reference=plain["index"],
    )
    ta.publish_companion_surface(store_root, bundles, companion)
    stats = ta.validate_companion(
        companion, root=store_root, store=store
    )
    costs = ta.storage_cost(store_root, companion)

    before_calls = dict(renders)
    plain_resume = run_corpus(
        store_root,
        manifest,
        request_template(),
        plain_renderer,
        indices=INDICES,
        resume=plain["run_id"],
    )
    traced_resume = run_corpus(
        store_root,
        manifest,
        traced_template,
        traced_renderer,
        indices=INDICES,
        resume=traced["run_id"],
    )
    additional = {
        key: renders[key] - before_calls[key] for key in renders
    }
    require(
        additional == {"plain": 0, "traced": 0},
        "resume rendered again",
    )
    require(
        plain_resume["counts"]["resume"] == 2
        and traced_resume["counts"]["resume"] == 2,
        "resume did not reuse both cases",
    )
    require(
        plain_resume["index"] == plain["index"]
        and traced_resume["index"] == traced["index"],
        "resume changed an index",
    )
    restats = ta.validate_companion(companion, root=store_root, store=store)

    def case_summary(envelope, index_document):
        summary = {}
        for case in index_document["cases"]:
            reference = case["artifact"]
            record = loads(
                (store_root / reference["ref"]).read_bytes()
            )
            summary[case["case_id"]] = dict(
                artifact=reference,
                audio_sha256=record["audio"]["value"]["file"]["sha256"],
                requested_traces=record["inputs"]["value"]["requested_traces"],
            )
        return summary

    plain_index = loads(read_reference(store_root, plain["index"]))
    traced_index = loads(read_reference(store_root, traced["index"]))
    receipt = dict(
        schema="torchsynth-trace-artifact-smoke",
        schema_version=1,
        generated_utc=subprocess.check_output(
            ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], text=True
        ).strip(),
        producer=dict(commit=identity["commit"], dirty=identity["dirty"]),
        host=host,
        image=image,
        registry=binding,
        selection=selection,
        runs=dict(
            audio_only=dict(
                run_id=plain["run_id"], counts=plain["counts"],
                index=plain["index"], resume_counts=plain_resume["counts"],
            ),
            traced=dict(
                run_id=traced["run_id"], counts=traced["counts"],
                index=traced["index"], resume_counts=traced_resume["counts"],
            ),
        ),
        companion=ta.companion_reference(companion),
        cases=dict(
            audio_only=case_summary(plain, plain_index),
            traced=case_summary(traced, traced_index),
        ),
        audio_identity_linkage=dict(
            same_audio_bytes_across_variants=True,
            distinct_artifact_ids=True,
        ),
        storage=dict(
            compression="none",
            companion_surface=costs,
            audio_bytes_per_case=176400 * 4,
            rehash_stats=restats,
        ),
        resume_evidence=dict(
            additional_render_attempts=additional,
            every_referenced_byte_rehashed=True,
        ),
        documents=dict(
            traced_index=traced_index,
            audio_only_index=plain_index,
            bundles=bundles,
            companion=companion,
        ),
        limits=[
            "Two development identities (global-0, global-1) only; no holdout access.",
            "The three normalization seams are observed by the landed worker's"
            " boundary profiler within the same render; they are recorded in"
            " each run receipt, not double-captured as artifact trace files.",
            "Traces are original uncompressed little-endian binary32 bytes;"
            " no codec, resampling, padding or casting was applied.",
            "The raw store lives under an ignored out/ path; the receipt"
            " authenticates metadata and recorded digests, not absent raw bytes.",
            "Verification-only recheck ran stdlib-only without TorchSynth,"
            " NumPy or rendering; store-dependent rehashing requires the"
            " retained raw store (--store).",
            "Bounded software evidence only: no full-corpus, scalar, fidelity,"
            " synthesis, layout, signoff or hardware playback claim.",
        ],
        warnings=[],
    )
    receipt_path = Path(receipt_path)
    require(
        not receipt_path.exists(),
        "publication receipt already exists; remove it deliberately first",
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    write_once(receipt_path, json_bytes(receipt))
    print(
        json.dumps(
            dict(
                companion=ta.companion_reference(companion),
                runs={k: v["run_id"] for k, v in receipt["runs"].items()},
                counts=dict(plain=plain["counts"], traced=traced["counts"]),
                storage=costs["aggregate"],
            ),
            indent=2,
        )
    )
    return receipt


def check_publication(receipt_path, store_root=None):
    """Strictly validate the committed publication against the live registry."""
    receipt_path = Path(receipt_path)
    if not receipt_path.exists():
        print(
            "publication: ABSENT — requires the host-gated qualified run"
            " (tools/qualify_trace_artifacts.py); absence is not a pass."
        )
        return 1
    receipt = loads(receipt_path.read_bytes())
    binding = registry_binding()
    require(
        receipt["registry"] == binding, "publication registry identity is stale"
    )
    require(
        receipt["selection"]["selection_sha256"]
        == selection_record()["selection_sha256"],
        "publication selection is stale",
    )
    documents = receipt["documents"]
    companion = documents["companion"]
    ta.validate_companion_document(companion, binding=binding)
    require(
        companion["index"]["sha256"]
        == digest(json_bytes(documents["traced_index"])),
        "embedded traced index does not match the pinned digest",
    )
    require(
        companion["variant_index"]["sha256"]
        == digest(json_bytes(documents["audio_only_index"])),
        "embedded audio-only index does not match the pinned digest",
    )
    validate_corpus_index(
        documents["traced_index"], require_complete=False
    )
    validate_corpus_index(
        documents["audio_only_index"], require_complete=False
    )
    embedded = {
        bundle["artifact"]["artifact_id"]: bundle
        for bundle in documents["bundles"]
    }
    require(
        len(embedded) == len(documents["bundles"]),
        "duplicate embedded bundle artifact",
    )
    for linked in companion["cases"]:
        if linked["bundle"] is None:
            continue
        artifact_id = linked["artifact"]["artifact_id"]
        require(artifact_id in embedded, "companion bundle missing from receipt")
        require(
            linked["bundle"]["sha256"]
            == digest(json_bytes(embedded[artifact_id])),
            "embedded bundle bytes do not match the pinned digest",
        )
        ta.validate_bundle_document(embedded[artifact_id], binding=binding)
    complete = [
        case for case in documents["traced_index"]["cases"]
        if case["status"] == "complete"
    ]
    require(
        len(complete) == len(embedded),
        "embedded bundle coverage is incomplete",
    )
    if store_root is not None:
        store = ArtifactStore(store_root)
        stats = ta.validate_companion(companion, root=store_root, store=store)
        print(json.dumps({"store_rehash_stats": stats}, indent=2))
    else:
        print(
            "store-dependent payload rehash not run (no --store provided);"
            " document-level validation passed."
        )
    print("publication: valid against the current registry identity")
    return 0

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-inputs",
        action="store_true",
        help="stdlib-only preregistered pins and negative controls",
    )
    parser.add_argument(
        "--check-publication",
        action="store_true",
        help="validate the committed receipt against the current registry",
    )
    parser.add_argument(
        "--store",
        type=Path,
        help="raw smoke store: required by the default run; optional for"
        " --check-publication payload rehashing",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=RECEIPT_PATH,
        help="publication receipt path (default: %(default)s)",
    )
    args = parser.parse_args()
    if args.check_inputs:
        print(json.dumps(check_inputs(), indent=2))
        return 0
    if args.check_publication:
        return check_publication(args.receipt, args.store)
    require(
        args.store is not None, "the qualified run requires --store ROOT"
    )
    run_smoke(args.store, args.receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
