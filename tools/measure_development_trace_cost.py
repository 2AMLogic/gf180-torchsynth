#!/usr/bin/env python3
"""Measure traced development-corpus storage and worker memory (#287).

Default mode renders the 96 development identities (global 0-95) with the
registry-ordered 29-trace production selection through the landed #24 traced
path (``tools/qualify_trace_artifacts.TracedDockerBackend``) into a fresh
store, with per-case worker telemetry, then publishes a bounded receipt whose
every row and total is recomputed from the verified store.

Gates are unchanged: a clean committed producer, the ratified DR-0006 runtime
publication, and ``qualify_trace_artifacts.host_admission()``. A host the gate
refuses is recorded as ``UNRUN`` (exit 2) before any store, Docker or render
access; ``UNRUN`` is never a pass.

``--check-inputs`` and ``--verify-receipt [--store ROOT]`` are stdlib-only.
Verification exits 0 only when the retained raw store was rehashed and every
figure recomputed; 2 for ``UNRUN``, an absent receipt, or a document-only
(``LIMITED``) check; 1 for any failure.

See spec/DEVELOPMENT-TRACE-COST.md.
"""

import argparse
import ast
import datetime
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import qualify_trace_artifacts as qta  # noqa: E402
from torchsynth_voice import development_trace_cost as dtc  # noqa: E402
from torchsynth_voice import trace_artifacts as ta  # noqa: E402
from torchsynth_voice.artifact_renderer import (  # noqa: E402
    PROFILE,
    QUALIFICATION_SHA256,
    digest,
    json_bytes,
    loads,
    project_identity,
    qualification,
    render_artifact,
    request_template,
    require,
)
from torchsynth_voice.artifacts import ValidationError  # noqa: E402
from torchsynth_voice.contract import repository_root  # noqa: E402
from torchsynth_voice.corpus import (  # noqa: E402
    read_bytes,
    read_reference,
    reference,
    run_corpus,
    run_reference,
    select_cases,
    write_once,
)
from torchsynth_voice.storage import ArtifactStore  # noqa: E402

RECEIPT_PATH = ROOT / dtc.RECEIPT_REF
MANIFEST_PATH = ROOT / dtc.MANIFEST_REF

_DRIVER_ENTRY = '\n\nif __name__ == "__main__":\n    main()\n'

# Python 3.9, executed inside the qualified image ahead of the unchanged #24
# driver body. Only clock and getrusage reads are added.
TELEMETRY_PRELUDE = '''\
# #287 telemetry prelude (Python 3.9); the unchanged #24 traced driver follows.
import resource as _telemetry_resource
import time as _telemetry_time

_TELEMETRY_MARKS = {"driver_prelude": _telemetry_time.monotonic()}
_TELEMETRY_RSS = {}


def _telemetry_rss():
    return _telemetry_resource.getrusage(
        _telemetry_resource.RUSAGE_SELF
    ).ru_maxrss


'''

TELEMETRY_ENTRY = '''


def _telemetry_main():
    import json as _json
    import platform as _platform

    _TELEMETRY_MARKS["driver_imports_done"] = _telemetry_time.monotonic()
    _TELEMETRY_RSS["after_imports"] = _telemetry_rss()
    original = worker.render_selected

    def timed(*args, **kwargs):
        _TELEMETRY_MARKS["render_call_start"] = _telemetry_time.monotonic()
        try:
            return original(*args, **kwargs)
        finally:
            _TELEMETRY_MARKS["render_call_end"] = _telemetry_time.monotonic()
            _TELEMETRY_RSS["after_render_call"] = _telemetry_rss()

    worker.render_selected = timed
    main()
    _TELEMETRY_MARKS["driver_end"] = _telemetry_time.monotonic()
    _TELEMETRY_RSS["driver_end"] = _telemetry_rss()
    document = {
        "schema": "torchsynth-worker-telemetry",
        "schema_version": 1,
        "clock": "time.monotonic",
        "rss_source": "resource.getrusage(resource.RUSAGE_SELF).ru_maxrss",
        "system": _platform.system(),
        "machine": _platform.machine(),
        "marks_seconds": _TELEMETRY_MARKS,
        "ru_maxrss_kib": _TELEMETRY_RSS,
    }
    Path("/output/telemetry.json").write_text(
        _json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\\n"
    )


if __name__ == "__main__":
    _telemetry_main()
'''


def measurement_driver_source():
    require(
        qta.DRIVER_SOURCE.endswith(_DRIVER_ENTRY),
        "#24 driver entry point changed; telemetry composition is stale",
    )
    body = qta.DRIVER_SOURCE[: -len(_DRIVER_ENTRY)]
    return TELEMETRY_PRELUDE + body + TELEMETRY_ENTRY


MEASURE_DRIVER_SOURCE = measurement_driver_source()


class MeasuredTracedDockerBackend(qta.TracedDockerBackend):
    """The #24 traced launch with the telemetry driver; gates unchanged."""

    driver_source = MEASURE_DRIVER_SOURCE

    def collect_telemetry(self, directory, launcher_elapsed_seconds):
        path = Path(directory) / "telemetry.json"
        raw = path.read_bytes() if path.is_file() else None
        return dtc.normalize_telemetry(raw, launcher_elapsed_seconds)


def production_expected():
    """The pins a production receipt must satisfy (qualified runtime, driver)."""
    return dtc.expected_pins(
        telemetry_driver_sha256=digest(MEASURE_DRIVER_SOURCE.encode())
    )


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def check_inputs():
    """Stdlib-only pins: selection, identities, holdout refusal, driver text."""
    selection = dtc.selection_binding()
    require(
        selection == qta.selection_record(),
        "selection formula drifted from the #24 smoke selection",
    )
    require(len(selection["requested_traces"]) == 29, "selection is not 29 traces")
    pins = production_expected()
    manifest = loads(MANIFEST_PATH.read_bytes())
    cases = select_cases(manifest, indices=pins["indices"])
    require(
        [c["sound_index"] for c in cases] == list(range(96))
        and all(c["split"] == "development" for c in cases),
        "development identities are not exactly 0-95",
    )
    require(select_cases(manifest) == cases, "default development set changed")
    try:
        select_cases(manifest, indices=[96])
    except ValidationError:
        pass
    else:
        raise ValidationError("holdout identity was not refused")
    ast.parse(MEASURE_DRIVER_SOURCE, feature_version=(3, 9))
    require(
        qta.DRIVER_SOURCE[: -len(_DRIVER_ENTRY)] in MEASURE_DRIVER_SOURCE,
        "telemetry driver does not embed the unchanged #24 driver",
    )
    return dict(
        indices="0-95 (96 development identities)",
        selection=selection,
        registry=pins["registry"],
        manifest=pins["manifest"],
        telemetry_driver_sha256=digest(MEASURE_DRIVER_SOURCE.encode()),
    )


def _replaceable(path):
    if not path.exists():
        return True
    try:
        return loads(path.read_bytes()).get("status") == "unrun"
    except (ValidationError, ValueError):
        return False


def _preflight_receipt(path, store_root):
    """Refuse an unpublishable destination before any store mutation."""
    path = Path(path)
    require(not path.is_dir(), "receipt destination is a directory: " + str(path))
    require(
        _replaceable(path),
        "refusing to overwrite a retained measured receipt: " + str(path),
    )
    resolved, store = path.resolve(), Path(store_root).resolve()
    require(
        resolved != store and store not in resolved.parents,
        "receipt destination must be outside the measurement store",
    )


def _publish_receipt(path, receipt):
    path = Path(path)
    require(
        _replaceable(path),
        "refusing to overwrite a retained measured receipt: " + str(path),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    write_once(path, json_bytes(receipt))


def run_campaign(
    store_root,
    receipt_path,
    *,
    resume=None,
    command=None,
    admission=qta.host_admission,
    backend_factory=MeasuredTracedDockerBackend,
    template=None,
    indices=dtc.DEVELOPMENT_INDICES,
    selection=None,
    expected=None,
):
    """Freeze, gate, render, account, self-verify, publish.

    Keyword overrides other than ``store_root``, ``receipt_path``, ``resume``
    and ``command`` exist for synthetic tests; the CLI never passes them, and
    a receipt produced with them cannot satisfy the production pins.
    """
    command = list(command or [sys.executable, *sys.argv])
    expected = production_expected() if expected is None else expected
    _preflight_receipt(receipt_path, store_root)
    identity = project_identity(repository_root())
    require(not identity["dirty"], "measurement requires a clean committed checkout")
    publication, _ = qualification()
    selection = dtc.selection_binding() if selection is None else selection
    if template is None:
        template = request_template(
            trace_registry_version=ta.registry_binding()["token"],
            requested_traces=selection["requested_traces"],
        )
    plan = dtc.freeze_plan(
        project_git=template["project_git"],
        template=template,
        qualification_sha256=QUALIFICATION_SHA256,
        image=publication["provenance"]["image"]["Id"],
        profile=PROFILE,
        driver_sha256=digest(MEASURE_DRIVER_SOURCE.encode()),
        indices=indices,
        selection=selection,
    )
    started = utc_now()
    try:
        host, image = admission(publication)
    except Exception as error:  # the gate's refusal, whatever its form
        reason = (
            "host_admission() refused before any store, Docker or render access: "
            + type(error).__name__ + ": " + str(error)
        )
        receipt = dtc.unrun_receipt(
            plan=plan,
            execution=dict(
                outcome="unrun",
                admitted=False,
                reason=reason,
                host=dict(
                    system=platform.system(),
                    machine=platform.machine(),
                    platform=platform.platform(),
                ),
                command=command,
                attempted_utc=started,
                store_created=False,
            ),
        )
        dtc.verify_document(receipt, expected=expected)
        _publish_receipt(receipt_path, receipt)
        print(json.dumps(dict(status="unrun", reason=reason), indent=2))
        return 2, receipt
    require(image == plan["runtime"]["image"], "admitted image is not the frozen image")

    store_root = Path(store_root).resolve()
    plan_bytes = json_bytes(plan)
    if resume is None:
        require(not store_root.exists(), "measurement store must be fresh")
    else:
        require(
            read_bytes(store_root / dtc.PLAN_STORE_REF) == plan_bytes,
            "resume store was frozen under a different plan",
        )
    store = ArtifactStore(store_root)
    write_once(store_root / dtc.PLAN_STORE_REF, plan_bytes)
    plan_reference = reference(dtc.PLAN_STORE_REF, plan_bytes)

    renders = {"traced": 0}

    def renderer(request, store_):
        renders["traced"] += 1
        return render_artifact(request, store_, backend_factory())

    envelope = run_corpus(
        store_root,
        MANIFEST_PATH,
        template,
        renderer,
        indices=plan["indices"],
        resume=resume,
    )
    index = loads(read_reference(store_root, envelope["index"]))
    bundles = [
        qta.build_case_bundle(store, case)
        for case in index["cases"]
        if case["status"] == "complete"
    ]
    companion = ta.build_companion(
        index_reference=envelope["index"], index=index, bundles=bundles
    )
    ta.publish_companion_surface(store_root, bundles, companion)
    ta.validate_companion(companion, root=store_root, store=store)
    execution = dict(
        outcome="executed",
        admitted=True,
        host=host,
        image=image,
        command=command,
        started_utc=started,
        finished_utc=utc_now(),
        resumed_run=resume,
        renders_this_invocation=renders["traced"],
        store_custody="operator-retained raw store outside Git: " + str(store_root),
    )
    receipt = dtc.measured_receipt(
        plan=plan,
        plan_reference=plan_reference,
        envelope=envelope,
        run_reference=run_reference(envelope),
        index=index,
        companion=companion,
        root=store_root,
        execution=execution,
    )
    require(
        dtc.verify_store(receipt, store_root, expected=expected) == dtc.VERIFIED,
        "fresh receipt failed raw-store verification",
    )
    _publish_receipt(receipt_path, receipt)
    print(
        json.dumps(
            dict(
                status=receipt["status"],
                run_id=envelope["run_id"],
                counts=envelope["counts"],
                completed_cases_subtotal=receipt["storage"]["completed_cases_subtotal"],
                peak_rss_kib=receipt["memory"]["peak_rss_kib"],
            ),
            indent=2,
        )
    )
    return (0 if receipt["status"] == "complete" else 1), receipt


def verify_receipt(receipt_path, store_root=None, *, expected=None):
    receipt_path = Path(receipt_path)
    if not receipt_path.exists():
        print("receipt: ABSENT - the campaign has not been published; not a pass")
        return 2
    receipt = loads(receipt_path.read_bytes())
    expected = production_expected() if expected is None else expected
    if store_root is None:
        state = dtc.verify_document(receipt, expected=expected)
    else:
        state = dtc.verify_store(receipt, store_root, expected=expected)
    if state == dtc.UNRUN:
        print("receipt: UNRUN - no measurement was executed; not a pass")
        return 2
    if state == dtc.LIMITED:
        print(
            "receipt: LIMITED - document-level consistency only; raw bytes were"
            " not rehashed (no --store); not a measurement verification"
        )
        return 2
    print(
        "receipt: VERIFIED against the retained raw store (status "
        + receipt["status"] + ")"
    )
    return 0 if receipt["status"] == "complete" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-inputs", action="store_true",
                        help="stdlib-only frozen pins and negative controls")
    parser.add_argument("--verify-receipt", action="store_true",
                        help="verify the receipt; add --store to rehash raw bytes")
    parser.add_argument("--store", type=Path,
                        help="fresh measurement store (run) or retained store (verify)")
    parser.add_argument("--receipt", type=Path, default=RECEIPT_PATH,
                        help="receipt path (default: %(default)s); a measured receipt is"
                        " never overwritten, so give each partial or resumed"
                        " publication a new path outside the producer checkout")
    parser.add_argument("--resume", metavar="RUN_ID",
                        help="continue an interrupted run in the same store")
    args = parser.parse_args(argv)
    try:
        if args.check_inputs:
            print(json.dumps(check_inputs(), indent=2))
            return 0
        if args.verify_receipt:
            return verify_receipt(args.receipt, args.store)
        require(args.store is not None, "the measurement run requires --store ROOT")
        code, _ = run_campaign(args.store, args.receipt, resume=args.resume)
        return code
    except ValidationError as error:
        print("FAIL: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
