"""Manifest coverage, immutable indexes, attempt journals and holdout admission.

All verification is stdlib-only and read-only, including missing-root failures.
"""

from __future__ import annotations

import copy
import fcntl
import os
import re
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path

from .artifact_renderer import (
    digest,
    fixture,
    json_bytes,
    require,
    validate_binding,
    validate_request,
)
from .artifacts import (
    ValidationError,
    _structure,
    canonical_bytes,
    loads,
    validate_corpus_index,
    verify_sha256,
)
from .contract import repository_root
from .storage import ArtifactStore, _directory, _read_regular


class ReadOnlyStore(ArtifactStore):
    """Adapt the landed read-only verify method without its mkdir constructor.

    No shared storage API changes: this private consumer uses only verify.
    """

    def __init__(self, root):
        self.root = Path(os.path.abspath(root))

    def publish(self, source):
        raise ValidationError("read-only artifact reader")


def read_bytes(path):
    path = Path(os.path.abspath(path))
    with _directory(path.parent) as descriptor:
        return _read_regular(descriptor, path.name)


def reference(path, data):
    return dict(ref=path, sha256=digest(data), size_bytes=len(data))


def read_reference(root, ref):
    require(
        type(ref) is dict and set(ref) == {"ref", "sha256", "size_bytes"},
        "invalid file reference",
    )
    require(
        type(ref["ref"]) is str
        and re.fullmatch(
            r"[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*", ref["ref"]
        ),
        "unsafe reference",
    )
    data = read_bytes(Path(root) / ref["ref"])
    verify_sha256(data, ref["sha256"])
    require(len(data) == ref["size_bytes"], "reference byte count mismatch")
    return data


def write_once(path, data):
    """Publish complete bytes without replacing an existing object."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _directory(path.parent):
        pass
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            require(read_bytes(path) == data, "immutable publication collision")
        os.unlink(temporary)
        with _directory(path.parent) as fd:
            os.fsync(fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def select_cases(manifest, *, indices=None, mode="development"):
    """Expand manifest metadata only; reject holdout before any case resolution."""
    data = (
        loads(read_bytes(manifest)) if isinstance(manifest, (Path, str)) else manifest
    )
    require(
        type(data) is dict
        and set(data)
        == {"schema_version", "name", "profile", "identity", "cases", "rules", "note"},
        "manifest fields mismatch",
    )
    require(
        data["schema_version"] == 1
        and data["identity"] == "global_sound_index"
        and data["profile"] == "torchsynth-1-voice-default",
        "unsupported manifest",
    )
    require(mode in ("development", "holdout"), "invalid partition")
    expanded = {}
    for item in data["cases"]:
        require(
            set(item) == {"start_inclusive", "stop_exclusive", "split"},
            "manifest range fields",
        )
        start, stop, split = (
            item["start_inclusive"],
            item["stop_exclusive"],
            item["split"],
        )
        require(
            type(start) is int
            and type(stop) is int
            and 0 <= start < stop <= 128
            and split in ("development", "holdout"),
            "invalid manifest range",
        )
        for index in range(start, stop):
            require(index not in expanded, "duplicate or overlapping manifest identity")
            require(
                split == ("development" if index < 96 else "holdout"),
                "manifest partition mismatch",
            )
            expanded[index] = dict(sound_index=index, split=split)
    require(set(expanded) == set(range(128)), "manifest missing or extra identities")
    require(
        data["rules"]
        == dict(
            holdout_is_blind_until_thresholds_are_frozen=True,
            case_count=128,
            development_count=96,
            holdout_count=32,
        ),
        "manifest count mismatch",
    )
    chosen = (
        [i for i, c in expanded.items() if c["split"] == mode]
        if indices is None
        else list(indices)
    )
    require(
        chosen and all(type(i) is int and i in expanded for i in chosen),
        "out-of-manifest identity",
    )
    require(len(chosen) == len(set(chosen)), "duplicate requested identity")
    if mode == "development":
        require(all(i < 96 for i in chosen), "holdout requires explicit admission")
    require(
        all(expanded[i]["split"] == mode for i in chosen), "mixed partition selection"
    )
    return [expanded[i] for i in sorted(chosen)]


def _admit(root, manifest_hash, selection, template, freeze, *, resume, audit_root):
    if selection[0]["split"] == "development":
        require(
            freeze is None and audit_root is None, "holdout options on development run"
        )
        return None
    require(
        freeze is not None and audit_root is not None,
        "holdout requires frozen rubric and audit root",
    )
    data = read_bytes(freeze)
    rubric = loads(data)
    require(
        type(rubric) is dict
        and set(rubric) == {"schema", "schema_version", "frozen", "rubric"}
        and rubric["schema"] == "torchsynth-frozen-rubric"
        and rubric["schema_version"] == 1
        and rubric["frozen"] is True
        and type(rubric["rubric"]) is dict
        and rubric["rubric"],
        "missing valid rubric freeze record",
    )
    ledger = Path(audit_root)
    expected = dict(
        schema="torchsynth-holdout-admission",
        schema_version=1,
        corpus_manifest_sha256=manifest_hash,
        frozen_rubric_sha256=digest(data),
        selection=selection,
        template_sha256=digest(canonical_bytes(template)),
    )
    path = ledger / (manifest_hash + ".json")
    if resume:
        existing = loads(read_bytes(path))
        require(
            set(existing) == set(expected) | {"run_id"}
            and all(existing[k] == v for k, v in expected.items())
            and existing["run_id"] == root.name,
            "holdout resume does not match original exposure",
        )
    else:
        ledger.mkdir(parents=True, exist_ok=True)
        # Exclusive reservation is held through the durable admission write.
        with (ledger / (manifest_hash + ".lock")).open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            require(not path.exists(), "holdout one-shot exposure already admitted")
            write_once(path, json_bytes(dict(expected, run_id=root.name)))
    admission = read_bytes(path)
    # The run carries exact audit/freeze bytes; the external ledger is never rewritten.
    write_once(root / "admission.json", admission)
    write_once(root / "frozen-rubric.json", data)
    return reference("runs/" + root.name + "/admission.json", admission)


def _request(template, case):
    return dict(copy.deepcopy(template), fixture=fixture(case["sound_index"]))


def _verify_artifact(root, ref, request):
    require(
        type(ref) is dict and set(ref) == {"artifact_id", "sha256", "ref"},
        "artifact reference fields mismatch",
    )
    require(
        ref["ref"] == "artifacts/" + ref["artifact_id"] + "/metadata.json",
        "artifact locator mismatch",
    )
    stored = ReadOnlyStore(root).verify(ref["artifact_id"], sha256=ref["sha256"])
    record = loads(read_bytes(stored.path / "metadata.json"))
    verify_sha256(read_bytes(stored.path / "metadata.json"), ref["sha256"])
    validate_binding(record, request)
    return record


def _journal(root):
    directory = root / "events"
    if not directory.exists():
        return []
    names = {p.name for p in directory.iterdir() if not p.name.startswith(".pending-")}
    require(
        all(re.fullmatch(r"[0-9]{6}-(start|finish).json", n) for n in names),
        "unexpected journal entry",
    )
    starts = sorted(n for n in names if n.endswith("-start.json"))
    require(
        starts == [f"{i:06}-start.json" for i in range(len(starts))],
        "journal sequence gap",
    )
    require(
        all(n.replace("-finish", "-start") in names for n in names),
        "finish without started attempt",
    )
    events = []
    for name in starts:
        start = loads(read_bytes(directory / name))
        require(
            set(start) == {"sequence", "case_id", "kind"}
            and start["sequence"] == len(events),
            "invalid attempt start",
        )
        finish = directory / name.replace("-start", "-finish")
        if finish.exists():
            event = loads(read_bytes(finish))
            require(
                all(event.get(k) == v for k, v in start.items()),
                "attempt/start mismatch",
            )
        else:
            event = dict(
                start,
                status="failed",
                failure="interrupted-attempt",
                artifact=None,
                elapsed_seconds=None,
                receipt={},
            )
        events.append(event)
    return events


def _counts(selection, events):
    latest = {event["case_id"]: event for event in events}
    renders = Counter(e["case_id"] for e in events if e["kind"] == "render")
    success = sum(e["status"] == "complete" for e in latest.values())
    return dict(
        expected=len(selection),
        observed=success,
        success=success,
        failure=len(selection) - success,
        attempt=sum(renders.values()),
        retry=sum(n - 1 for n in renders.values()),
        resume=sum(e["kind"] == "resume" for e in events),
    )


def _index(plan, events):
    latest = {e["case_id"]: e for e in events}
    cases = []
    for case in plan["selection"]:
        case_id = "global-" + str(case["sound_index"])
        event = latest.get(case_id)
        complete = event is not None and event["status"] == "complete"
        cases.append(
            dict(
                case_id=case_id,
                split=case["split"],
                fixture=fixture(case["sound_index"]),
                status="complete" if complete else "failed",
                artifact=event["artifact"] if complete else None,
                warnings=[],
                failures=[]
                if complete
                else [event["failure"] if event else "not-attempted"],
            )
        )
    success = sum(c["status"] == "complete" for c in cases)
    return dict(
        schema="torchsynth-corpus-index",
        schema_version=1,
        corpus_id="ci1-"
        + digest(
            canonical_bytes({k: plan[k] for k in ("manifest", "selection", "template")})
        ),
        corpus_manifest_sha256=plan["manifest"]["sha256"],
        profile=plan["template"]["profile"]["name"],
        runtime_lock_sha256=plan["template"]["runtime"]["lock_sha256"],
        status="complete" if success == len(cases) else "failed",
        expected_case_count=len(cases),
        observed_case_count=success,
        cases=cases,
        warnings=[],
        failures=[] if success == len(cases) else ["incomplete-corpus"],
    )


def validate_run(envelope, plan, index, *, root=None):
    schema = loads(
        (repository_root() / "spec/schemas/corpus-run-v1.schema.json").read_bytes()
    )
    canonical_bytes(envelope)
    _structure(envelope, schema, "corpus-run-v1.schema.json")
    require(
        set(plan)
        == {
            "schema",
            "schema_version",
            "run_id",
            "manifest",
            "selection",
            "template",
            "admission",
        }
        and plan["schema"] == "torchsynth-corpus-plan"
        and plan["schema_version"] == 1,
        "plan shape mismatch",
    )
    require(
        plan["run_id"] == envelope["run_id"]
        and plan["admission"] == envelope["admission"],
        "run/plan linkage mismatch",
    )
    selection = plan["selection"]
    require(
        selection and selection == sorted(selection, key=lambda c: c["sound_index"]),
        "selection order mismatch",
    )
    events = envelope["attempts"]
    require(
        [e["sequence"] for e in events] == list(range(len(events))),
        "attempt sequence mismatch",
    )
    valid_cases = {"global-" + str(c["sound_index"]): c for c in selection}
    require(len(valid_cases) == len(selection), "duplicate planned identity")
    completed = {}
    artifacts = {}
    for event in events:
        require(event["case_id"] in valid_cases, "out-of-manifest attempt")
        complete = event["status"] == "complete"
        require(
            complete == (event["artifact"] is not None)
            and complete == (event["failure"] is None),
            "attempt result contradiction",
        )
        if event["kind"] == "resume":
            require(
                complete and event["case_id"] in completed,
                "resume without a completed render",
            )
            require(
                event["artifact"] == completed[event["case_id"]],
                "resume changed the exact artifact reference",
            )
        else:
            require(
                event["case_id"] not in completed, "recomputation of completed case"
            )
        if complete:
            completed[event["case_id"]] = event["artifact"]
            if root is not None:
                record = _verify_artifact(
                    root,
                    event["artifact"],
                    _request(plan["template"], valid_cases[event["case_id"]]),
                )
                artifacts[record["artifact_id"]] = record
    for case in selection:
        validate_request(_request(plan["template"], case))
    require(envelope["counts"] == _counts(selection, events), "run count mismatch")
    require(
        all(
            e["elapsed_seconds"] is not None or e["failure"] == "interrupted-attempt"
            for e in events
        ),
        "missing measured elapsed time",
    )
    require(envelope["elapsed_seconds"] == _elapsed(events), "elapsed time mismatch")
    require(index == _index(plan, events), "index identity/coverage/journal mismatch")
    require(envelope["status"] == index["status"], "run/index status mismatch")
    validate_corpus_index(
        index, require_complete=False, artifacts=artifacts if root else None
    )
    if root is not None:
        manifest = loads(read_reference(root, plan["manifest"]))
        require(
            select_cases(
                manifest,
                indices=[c["sound_index"] for c in selection],
                mode=selection[0]["split"],
            )
            == selection,
            "manifest selection mismatch",
        )
        if plan["admission"] is not None:
            admission = loads(read_reference(root, plan["admission"]))
            require(
                admission["selection"] == selection
                and admission["run_id"] == plan["run_id"]
                and admission["corpus_manifest_sha256"] == plan["manifest"]["sha256"]
                and admission["template_sha256"]
                == digest(canonical_bytes(plan["template"])),
                "admission linkage mismatch",
            )
            verify_sha256(
                read_bytes(Path(root) / "runs" / plan["run_id"] / "frozen-rubric.json"),
                admission["frozen_rubric_sha256"],
            )
        else:
            require(
                all(c["split"] == "development" for c in selection),
                "holdout admission missing",
            )


def verify_run(root, run_id, *, allow_holdout=False, expected_sha256=None):
    """Rehash latest immutable run snapshot and all artifacts; never create paths."""
    require(
        type(run_id) is str and re.fullmatch(r"[a-f0-9]{32}", run_id), "invalid run ID"
    )
    root = Path(root)
    directory = root / "runs" / run_id / "summaries"
    names = sorted(
        p.name for p in directory.iterdir() if not p.name.startswith(".pending-")
    )
    require(
        names and all(re.fullmatch(r"[0-9]{6}.json", n) for n in names),
        "run summary missing or invalid",
    )
    envelope_bytes = read_bytes(directory / names[-1])
    if expected_sha256 is not None:
        verify_sha256(envelope_bytes, expected_sha256)
    envelope = loads(envelope_bytes)
    plan = loads(read_reference(root, envelope["plan"]))
    require(
        allow_holdout or all(c["split"] == "development" for c in plan["selection"]),
        "holdout verification requires explicit mode",
    )
    index = loads(read_reference(root, envelope["index"]))
    validate_run(envelope, plan, index, root=root)
    require(
        envelope["attempts"] == _journal(root / "runs" / run_id),
        "journal differs from published run",
    )
    return envelope


def run_corpus(
    root,
    manifest,
    template,
    render,
    *,
    indices=None,
    mode="development",
    resume=None,
    frozen_rubric=None,
    audit_root=None,
):
    """Render each selected case once, or resume its verified exact reference.

    render(request, store) returns RenderProduct. Exceptions retain failed cases;
    interrupts leave durable started attempts for explicit same-run continuation.
    """
    manifest_data = read_bytes(manifest)
    selection = select_cases(loads(manifest_data), indices=indices, mode=mode)
    for case in selection:
        validate_request(_request(template, case))
    run_id = resume or uuid.uuid4().hex
    require(
        type(run_id) is str and re.fullmatch(r"[a-f0-9]{32}", run_id), "invalid run ID"
    )
    root = Path(os.path.abspath(root))
    run = root / "runs" / run_id
    manifest_ref = reference(
        "manifests/" + digest(manifest_data) + ".json", manifest_data
    )
    # Refusal must precede ArtifactStore's directory-creating constructor.
    if mode == "holdout":
        require(
            frozen_rubric is not None and audit_root is not None,
            "holdout requires frozen rubric and audit root",
        )
    if resume:
        prior = loads(read_bytes(run / "plan.json"))
        require(
            prior["selection"] == selection
            and prior["template"] == template
            and prior["manifest"] == manifest_ref,
            "stale resume request/source/runtime/configuration",
        )
    admission = _admit(
        run,
        digest(manifest_data),
        selection,
        template,
        frozen_rubric,
        resume=bool(resume),
        audit_root=audit_root,
    )
    store = ArtifactStore(root)
    run.mkdir(parents=True, exist_ok=True)
    with (run / ".run.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = dict(
            schema="torchsynth-corpus-plan",
            schema_version=1,
            run_id=run_id,
            manifest=manifest_ref,
            selection=selection,
            template=template,
            admission=admission,
        )
        plan_data = json_bytes(plan)
        write_once(root / manifest_ref["ref"], manifest_data)
        write_once(run / "plan.json", plan_data)
        events = _journal(run)
        if events:
            interim = _envelope(plan, plan_data, _index(plan, events), events)
            validate_run(interim, plan, _index(plan, events), root=root)
        latest = {e["case_id"]: e for e in events}
        for case in selection:
            case_id = "global-" + str(case["sound_index"])
            request = _request(template, case)
            previous = latest.get(case_id)
            reuse = previous is not None and previous["status"] == "complete"
            if reuse:
                _verify_artifact(root, previous["artifact"], request)
            start = dict(
                sequence=len(events),
                case_id=case_id,
                kind="resume" if reuse else "render",
            )
            prefix = run / "events" / f"{len(events):06}"
            write_once(Path(str(prefix) + "-start.json"), json_bytes(start))
            began = time.monotonic()
            try:
                product = None if reuse else render(request, store)
                ref = previous["artifact"] if reuse else product.reference
                _verify_artifact(root, ref, request)
                event = dict(
                    start,
                    status="complete",
                    artifact=ref,
                    failure=None,
                    receipt={} if reuse else product.receipt,
                )
            except Exception as error:
                # Integrity failures on previously completed artifacts never reach here.
                code = re.sub(r"[^A-Za-z0-9._-]", "-", type(error).__name__)
                event = dict(
                    start,
                    status="failed",
                    artifact=None,
                    failure="render-" + code,
                    receipt={"error": str(error)},
                )
            event["elapsed_seconds"] = time.monotonic() - began
            write_once(Path(str(prefix) + "-finish.json"), json_bytes(event))
            events.append(event)
        index = _index(plan, events)
        envelope = _envelope(plan, plan_data, index, events)
        validate_run(envelope, plan, index, root=root)
        write_once(root / envelope["index"]["ref"], json_bytes(index))
        write_once(run / "summaries" / f"{len(events):06}.json", json_bytes(envelope))
        return envelope


def _envelope(plan, plan_data, index, events):
    index_data = json_bytes(index)
    return dict(
        schema="torchsynth-corpus-run",
        schema_version=1,
        run_id=plan["run_id"],
        plan=reference("runs/" + plan["run_id"] + "/plan.json", plan_data),
        index=reference("indexes/" + digest(index_data) + ".json", index_data),
        admission=plan["admission"],
        status=index["status"],
        counts=_counts(plan["selection"], events),
        elapsed_seconds=_elapsed(events),
        attempts=events,
    )


def _elapsed(events):
    if any(e["elapsed_seconds"] is None for e in events):
        return None
    return sum(e["elapsed_seconds"] for e in events)


def run_reference(envelope):
    return reference(
        f"runs/{envelope['run_id']}/summaries/{len(envelope['attempts']):06}.json",
        json_bytes(envelope),
    )
