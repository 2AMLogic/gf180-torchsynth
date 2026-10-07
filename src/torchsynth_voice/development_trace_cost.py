"""Traced development-corpus storage and memory accounting (#287).

Stdlib-only accounting and verification for the 96-case traced development
measurement produced by ``tools/measure_development_trace_cost.py``:

* a frozen measurement plan (manifest, producer, runtime, registry, selection
  and the exact development identities 0-95) fixed before any render;
* per-case rows for every planned identity with an explicit
  ``completed`` / ``failed`` / ``unrun`` state;
* logical file-byte accounting recomputed from the verified store, split into
  trace payloads, audio, artifact metadata, bundles, the shared companion and
  other corpus metadata, with filesystem-allocated bytes reported separately;
* per-process worker telemetry (``ru_maxrss`` in KiB, monotonic boundaries)
  that stays ``missing`` rather than zero when it was not collected;
* a verifier that, given the retained raw store, rehashes every referenced
  byte and recomputes every row and total; without the store it states a
  limited, document-only claim.

Nothing here imports Torch, TorchSynth, NumPy or Docker, renders, or writes
into a store. See spec/DEVELOPMENT-TRACE-COST.md.
"""

from __future__ import annotations

import math
import os
import re
import stat
import statistics
from pathlib import Path

from . import trace_artifacts as ta
from . import trace_registry
from .artifact_renderer import digest, json_bytes, require
from .artifacts import ValidationError, canonical_bytes, loads
from .contract import repository_root
from .corpus import ReadOnlyStore, read_bytes, read_reference, verify_run

SCHEMA = "torchsynth-development-trace-cost"
PLAN_SCHEMA = "torchsynth-development-trace-cost-plan"
TELEMETRY_SCHEMA = "torchsynth-worker-telemetry"
SCHEMA_VERSION = 1
RECEIPT_REF = "sim/reference/development-trace-cost-v1.json"
MANIFEST_REF = "spec/reference/corpus-v0.json"
# Inside the measurement store; written once before the first render.
PLAN_STORE_REF = "development-trace-cost/plan.json"
DEVELOPMENT_INDICES = tuple(range(96))
STATES = ("completed", "failed", "unrun")
STATUSES = ("unrun", "partial", "complete")

RSS_SOURCE = "resource.getrusage(resource.RUSAGE_SELF).ru_maxrss"
RSS_UNIT = "KiB"
CLOCK = "time.monotonic"
MARKS = (
    "driver_prelude",
    "driver_imports_done",
    "render_call_start",
    "render_call_end",
    "driver_end",
)
RSS_POINTS = ("after_imports", "after_render_call", "driver_end")
# Each derived elapsed value is the difference of two container marks.
WORKER_INTERVALS = {
    "worker_imports_seconds": ("driver_prelude", "driver_imports_done"),
    "worker_render_call_seconds": ("render_call_start", "render_call_end"),
    "worker_serialization_seconds": ("render_call_end", "driver_end"),
    "worker_driver_seconds": ("driver_prelude", "driver_end"),
}
TELEMETRY_DECLARATION = dict(
    rss_source=RSS_SOURCE,
    rss_unit=RSS_UNIT,
    rss_scope=(
        "the single fresh container worker process running the traced driver;"
        " ru_maxrss is that process's monotonic peak resident set, read inside"
        " the container. Includes interpreter startup, imports, Voice setup,"
        " rendering, capture and output serialization up to driver_end. Excludes"
        " the host launcher, Docker and the emulation layer outside the guest"
        " process accounting, and any simultaneous corpus memory: one process"
        " per case, never concurrent, so per-case peaks are not summed."
    ),
    clock=CLOCK,
    marks=dict(
        driver_prelude="first driver statement, before the driver imports the"
        " landed worker, capture provider and registry (interpreter startup"
        " precedes it and is excluded from worker intervals)",
        driver_imports_done="driver imports complete, before main()",
        render_call_start="entry of render_artifact.render_selected",
        render_call_end="return of render_artifact.render_selected"
        " (Voice construction, render and capture inside the worker call)",
        driver_end="after the driver wrote all audio, trace, descriptor and"
        " observation files (telemetry file itself excluded)",
    ),
    host_intervals=dict(
        launcher_container_seconds="host monotonic time around the docker run"
        " subprocess: container create/start/teardown, emulation and the"
        " whole driver; not worker-only time",
        attempt_elapsed_seconds="landed corpus attempt time: request"
        " validation, the launcher, host-side verification and store"
        " publication; an end-to-end attempt, not rendering time",
    ),
)

_HEX64 = r"[a-f0-9]{64}"
_ID = r"[A-Za-z0-9_-]+"
_CATEGORIES = (
    "trace_payload",
    "audio",
    "artifact_metadata",
    "bundle",
    "companion",
    "corpus_metadata",
)
_PATTERNS = (
    (re.compile(rf"artifacts/{_ID}/traces/trace-[0-9]+\.bin"), "trace_payload"),
    (re.compile(rf"artifacts/{_ID}/audio\.f32le"), "audio"),
    (re.compile(rf"artifacts/{_ID}/metadata\.json"), "artifact_metadata"),
    (re.compile(rf"trace-bundles/{_ID}\.json"), "bundle"),
    (re.compile(rf"trace-companions/{_HEX64}\.json"), "companion"),
    (re.compile(rf"manifests/{_HEX64}\.json"), "corpus_metadata"),
    (re.compile(rf"indexes/{_HEX64}\.json"), "corpus_metadata"),
    (re.compile(r"runs/[a-f0-9]{32}/plan\.json"), "corpus_metadata"),
    (re.compile(r"runs/[a-f0-9]{32}/\.run\.lock"), "corpus_metadata"),
    (re.compile(r"runs/[a-f0-9]{32}/events/[0-9]{6}-(start|finish)\.json"),
     "corpus_metadata"),
    (re.compile(r"runs/[a-f0-9]{32}/summaries/[0-9]{6}\.json"), "corpus_metadata"),
    (re.compile(re.escape(PLAN_STORE_REF)), "corpus_metadata"),
    (re.compile(r"\.publish\.lock"), "corpus_metadata"),
)
_CASE_STORAGE_KEYS = (
    "trace_payload_files",
    "trace_payload_bytes",
    "audio_bytes",
    "artifact_metadata_bytes",
    "bundle_bytes",
    "artifact_logical_bytes",
    "case_logical_bytes",
)


# --------------------------------------------------------------------------
# Plan pins


def selection_binding(document=None):
    """The registry-ordered 29-trace production selection and its digest.

    Same formula as ``tools/qualify_trace_artifacts.selection_record``; the
    measurement runner asserts the two agree.
    """
    document = trace_registry.load_registry() if document is None else document
    selection = ta.production_selection(document)
    excluded = [
        t["name"] for t in document["traces"] if t["name"] in ta.NORMALIZATION_SEAMS
    ]
    return dict(
        requested_traces=selection,
        excluded_normalization_seams=excluded,
        selection_sha256=digest(canonical_bytes(selection)),
    )


def manifest_binding():
    data = (repository_root() / MANIFEST_REF).read_bytes()
    return dict(ref=MANIFEST_REF, sha256=digest(data))


def case_id(index):
    return "global-" + str(index)


def expected_pins():
    """Live pins a production receipt must carry: 96 identities, 29 traces."""
    return dict(
        indices=list(DEVELOPMENT_INDICES),
        selection=selection_binding(),
        registry=ta.registry_binding(),
        manifest=manifest_binding(),
    )


def freeze_plan(
    *,
    project_git,
    template,
    qualification_sha256,
    image,
    profile,
    driver_sha256,
    indices=DEVELOPMENT_INDICES,
    selection=None,
):
    """The measurement plan, fixed before admission or any render."""
    selection = selection_binding() if selection is None else selection
    indices = list(indices)
    require(
        indices == sorted(set(indices)) and indices,
        "plan identities must be unique and ascending",
    )
    require(all(type(i) is int and 0 <= i < 96 for i in indices),
            "development plan refuses holdout identities")
    require(
        template["requested_traces"] == selection["requested_traces"]
        and template["trace_registry_version"] == ta.registry_binding()["token"],
        "template does not carry the frozen selection and registry token",
    )
    return dict(
        schema=PLAN_SCHEMA,
        schema_version=SCHEMA_VERSION,
        producer=dict(commit=project_git["commit"], dirty=project_git["dirty"]),
        project_git=dict(project_git),
        manifest=manifest_binding(),
        indices=indices,
        case_ids=[case_id(i) for i in indices],
        selection=selection,
        registry=ta.registry_binding(),
        runtime=dict(
            profile=profile,
            qualification_ref="sim/reference/repeatability-runtime.json",
            qualification_sha256=qualification_sha256,
            image=image,
            request_runtime=template["runtime"],
        ),
        template_sha256=digest(canonical_bytes(template)),
        telemetry=dict(TELEMETRY_DECLARATION, driver_sha256=driver_sha256),
        holdout_access=False,
    )


def check_plan(plan, expected=None):
    """Structural plan checks plus agreement with the live production pins."""
    require(type(plan) is dict and plan.get("schema") == PLAN_SCHEMA,
            "not a development trace-cost plan")
    require(plan["schema_version"] == SCHEMA_VERSION, "plan version mismatch")
    expected = expected_pins() if expected is None else expected
    require(plan["indices"] == expected["indices"],
            "plan identities differ from the frozen development set")
    require(plan["case_ids"] == [case_id(i) for i in plan["indices"]],
            "plan case IDs do not follow the identities")
    require(plan["selection"] == expected["selection"],
            "plan selection is stale or not the production selection")
    require(plan["registry"] == expected["registry"],
            "plan registry identity is stale")
    require(plan["manifest"] == expected["manifest"],
            "plan manifest identity is stale")
    require(plan["holdout_access"] is False, "holdout access declared")
    require(not plan["producer"]["dirty"] and not plan["project_git"]["dirty"],
            "plan producer checkout was dirty")
    require(
        {k: v for k, v in plan["telemetry"].items() if k != "driver_sha256"}
        == TELEMETRY_DECLARATION,
        "telemetry declaration changed",
    )
    require(re.fullmatch(_HEX64, plan["telemetry"]["driver_sha256"]) is not None,
            "telemetry driver digest missing")
    return True


# --------------------------------------------------------------------------
# Worker telemetry


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def normalize_telemetry(raw, launcher_container_seconds):
    """Container telemetry document -> receipt telemetry; never invents values.

    Returns ``{"state": "missing", "reason": ...}`` for absent or unusable
    telemetry rather than raising or substituting zero.
    """
    if raw is None:
        return dict(state="missing", reason="worker telemetry file absent")
    try:
        document = loads(raw) if isinstance(raw, (bytes, str)) else raw
        require(
            type(document) is dict
            and document.get("schema") == TELEMETRY_SCHEMA
            and document.get("schema_version") == SCHEMA_VERSION,
            "unknown telemetry document",
        )
        require(document.get("clock") == CLOCK, "telemetry clock mismatch")
        require(document.get("rss_source") == RSS_SOURCE, "rss source mismatch")
        require(
            document.get("system") == "Linux",
            "ru_maxrss unit is only declared (KiB) for a Linux worker",
        )
        marks = document.get("marks_seconds")
        rss = document.get("ru_maxrss_kib")
        require(type(marks) is dict and set(marks) == set(MARKS),
                "telemetry marks incomplete")
        require(type(rss) is dict and set(rss) == set(RSS_POINTS),
                "telemetry RSS points incomplete")
        value = dict(
            state="present",
            rss_unit=RSS_UNIT,
            system=document["system"],
            machine=document.get("machine"),
            marks_seconds={k: marks[k] for k in MARKS},
            rss_kib={k: rss[k] for k in RSS_POINTS},
            peak_rss_kib=rss["driver_end"],
            elapsed_seconds=dict(
                {
                    name: marks[end] - marks[start]
                    for name, (start, end) in WORKER_INTERVALS.items()
                    if _finite(marks[end]) and _finite(marks[start])
                },
                launcher_container_seconds=launcher_container_seconds,
            ),
        )
        validate_telemetry(value)
        return value
    except (ValidationError, ValueError, TypeError, KeyError) as error:
        return dict(state="missing", reason="invalid worker telemetry: " + str(error))


def validate_telemetry(value):
    """Recheck units, monotonic boundaries and derived intervals."""
    require(type(value) is dict, "telemetry must be an object")
    if value.get("state") == "missing":
        require(set(value) == {"state", "reason"} and type(value["reason"]) is str,
                "missing telemetry must carry only a reason")
        return False
    require(
        set(value) == {"state", "rss_unit", "system", "machine", "marks_seconds",
                       "rss_kib", "peak_rss_kib", "elapsed_seconds"}
        and value["state"] == "present",
        "telemetry fields mismatch",
    )
    require(value["rss_unit"] == RSS_UNIT and value["system"] == "Linux",
            "telemetry RSS unit is not Linux KiB")
    marks = value["marks_seconds"]
    require(type(marks) is dict and set(marks) == set(MARKS)
            and all(_finite(marks[k]) for k in MARKS),
            "telemetry marks missing or non-finite")
    require(all(marks[a] <= marks[b] for a, b in zip(MARKS, MARKS[1:])),
            "telemetry boundaries are not monotonic")
    rss = value["rss_kib"]
    require(type(rss) is dict and set(rss) == set(RSS_POINTS)
            and all(type(rss[k]) is int and rss[k] > 0 for k in RSS_POINTS),
            "telemetry RSS must be positive integer KiB")
    require(all(rss[a] <= rss[b] for a, b in zip(RSS_POINTS, RSS_POINTS[1:])),
            "ru_maxrss is a monotonic peak; samples decreased")
    require(value["peak_rss_kib"] == rss["driver_end"],
            "peak RSS is not the driver_end process peak")
    elapsed = value["elapsed_seconds"]
    expected = {
        name: marks[end] - marks[start]
        for name, (start, end) in WORKER_INTERVALS.items()
    }
    launcher = elapsed.get("launcher_container_seconds")
    require(_finite(launcher) and launcher >= 0,
            "launcher interval missing or invalid")
    require(dict(expected, launcher_container_seconds=launcher) == elapsed,
            "derived worker intervals disagree with the marks")
    return True


# --------------------------------------------------------------------------
# Store accounting


def classify(relative):
    for pattern, category in _PATTERNS:
        if pattern.fullmatch(relative):
            return category
    raise ValidationError("unexpected store file: " + relative)


def _allocated(info):
    return info.st_blocks * 512 if hasattr(info, "st_blocks") else None


def store_inventory(root):
    """Every regular file in the store, classified exactly once.

    Logical bytes are file lengths; allocated bytes are ``st_blocks * 512``
    and depend on the filesystem. Symlinks, staging residue and unknown paths
    are refused, never skipped.
    """
    root = Path(os.path.abspath(root))
    totals = {c: dict(files=0, logical_bytes=0, allocated_bytes=0) for c in _CATEGORIES}
    artifacts = set()
    for directory, names, files in os.walk(root, followlinks=False):
        here = Path(directory)
        for name in list(names):
            info = os.lstat(here / name)
            require(stat.S_ISDIR(info.st_mode), "symlinked store directory: " + name)
        relative_dir = here.relative_to(root).as_posix()
        if relative_dir == ".staging":
            require(not names and not files, "staging residue in store")
        for name in files:
            path = here / name
            relative = path.relative_to(root).as_posix()
            info = os.lstat(path)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                    "store entry is not an unaliased regular file: " + relative)
            category = classify(relative)
            if relative.startswith("artifacts/"):
                artifacts.add(relative.split("/")[1])
            entry = totals[category]
            entry["files"] += 1
            entry["logical_bytes"] += info.st_size
            entry["allocated_bytes"] += _allocated(info) or 0
    logical = sum(v["logical_bytes"] for v in totals.values())
    allocated = sum(v["allocated_bytes"] for v in totals.values())
    return dict(
        categories=totals,
        total_files=sum(v["files"] for v in totals.values()),
        total_logical_bytes=logical,
        total_allocated_bytes=allocated,
        artifact_ids=sorted(artifacts),
    )


def logical_inventory(inventory):
    """The filesystem-independent part of an inventory."""
    return dict(
        categories={
            k: dict(files=v["files"], logical_bytes=v["logical_bytes"])
            for k, v in inventory["categories"].items()
        },
        total_files=inventory["total_files"],
        total_logical_bytes=inventory["total_logical_bytes"],
        artifact_ids=list(inventory["artifact_ids"]),
    )


def artifact_storage(store, artifact, bundle_reference, root):
    """Per-case logical bytes from the verified artifact and its bundle."""
    stored = store.verify(artifact["artifact_id"], sha256=artifact["sha256"])
    metadata = read_bytes(stored.path / "metadata.json")
    record = loads(metadata)
    audio = record["audio"]["value"]["file"]
    traces = record["traces"]["value"]
    paths = [stored.path / "metadata.json", stored.path / audio["ref"]]
    paths += [stored.path / ref["ref"] for ref in traces.values()]
    paths.append(Path(root) / bundle_reference["ref"])
    allocated = sum(_allocated(os.lstat(p)) or 0 for p in paths)
    trace_bytes = sum(ref["size_bytes"] for ref in traces.values())
    artifact_bytes = trace_bytes + audio["size_bytes"] + len(metadata)
    storage = dict(
        trace_payload_files=len(traces),
        trace_payload_bytes=trace_bytes,
        audio_bytes=audio["size_bytes"],
        artifact_metadata_bytes=len(metadata),
        bundle_bytes=bundle_reference["size_bytes"],
        artifact_logical_bytes=artifact_bytes,
        case_logical_bytes=artifact_bytes + bundle_reference["size_bytes"],
    )
    return storage, allocated, record


# --------------------------------------------------------------------------
# Rows and summaries


def _render_event(events, cid):
    """The attempt that produced the completed artifact (never a resume)."""
    chosen = None
    for event in events:
        if (event["case_id"] == cid and event["kind"] == "render"
                and event["status"] == "complete"):
            chosen = event
    return chosen


def _seams(receipt):
    observations = (receipt or {}).get("observations")
    if not observations:
        return None
    return dict(
        state="receipt-retained",
        names=list(ta.NORMALIZATION_SEAMS),
        pre_normalization_sha256=observations["pre_normalization_sha256"],
        pre_normalization_peak=observations["pre_normalization_peak"],
        normalization_gain_derived=observations["normalization_gain_derived"],
    )


def case_rows(plan, index, events, companion, *, store, root):
    """One row per planned identity, in plan order; no omission, no extras."""
    by_case = {}
    for case in index["cases"]:
        require(case["case_id"] not in by_case, "duplicate index case")
        by_case[case["case_id"]] = case
    require(list(by_case) == plan["case_ids"],
            "index identities differ from the frozen plan")
    linked = {c["case_id"]: c for c in companion["cases"]}
    require(list(linked) == plan["case_ids"],
            "companion identities differ from the frozen plan")
    rows = []
    allocated = {}
    for cid, sound_index in zip(plan["case_ids"], plan["indices"]):
        case = by_case[cid]
        attempts = sum(
            1 for e in events if e["case_id"] == cid and e["kind"] == "render"
        )
        row = dict(
            case_id=cid,
            sound_index=sound_index,
            state=None,
            render_attempts=attempts,
            artifact=None,
            failure=None,
            storage=None,
            telemetry=None,
            attempt_elapsed_seconds=None,
            normalization_seams=None,
        )
        if case["status"] == "complete":
            event = _render_event(events, cid)
            require(event is not None, "completed case without its render attempt")
            require(linked[cid]["bundle"] is not None, "completed case has no bundle")
            storage, allocated[cid], record = artifact_storage(
                store, case["artifact"], linked[cid]["bundle"], root
            )
            require(
                record["inputs"]["value"]["requested_traces"]
                == plan["selection"]["requested_traces"],
                "artifact traces differ from the frozen selection: " + cid,
            )
            receipt = event["receipt"]
            telemetry = receipt.get("worker_telemetry")
            row.update(
                state="completed",
                artifact=dict(case["artifact"]),
                storage=storage,
                telemetry=telemetry
                if telemetry is not None
                else dict(state="missing", reason="receipt carries no worker telemetry"),
                attempt_elapsed_seconds=event["elapsed_seconds"],
                normalization_seams=_seams(receipt),
            )
        elif case["failures"] == ["not-attempted"]:
            row.update(state="unrun", failure="not-attempted")
        else:
            row.update(state="failed", failure=", ".join(case["failures"]))
        rows.append(row)
    return rows, allocated


def _distribution(values):
    if not values:
        return None
    return dict(
        count=len(values),
        minimum=min(values),
        median=statistics.median(values),
        maximum=max(values),
    )


def summaries(rows):
    """Recomputable totals; per-process peaks are distributed, never summed."""
    completed = [r for r in rows if r["state"] == "completed"]
    subtotal = {k: sum(r["storage"][k] for r in completed) for k in _CASE_STORAGE_KEYS}
    present = [r for r in completed if r["telemetry"]["state"] == "present"]
    missing_telemetry = [r["case_id"] for r in completed if r not in present]
    memory = dict(
        unit=RSS_UNIT,
        scope="per-case worker process peak; distribution only, not a sum and"
        " not simultaneous corpus memory",
        cases_with_telemetry=len(present),
        cases_missing_telemetry=missing_telemetry,
        peak_rss_kib=_distribution([r["telemetry"]["peak_rss_kib"] for r in present]),
        after_render_call_rss_kib=_distribution(
            [r["telemetry"]["rss_kib"]["after_render_call"] for r in present]
        ),
        maximum_case=max(present, key=lambda r: r["telemetry"]["peak_rss_kib"])[
            "case_id"] if present else None,
    )
    timing = dict(
        attempt_elapsed_seconds=_distribution(
            [r["attempt_elapsed_seconds"] for r in completed]
        ),
        sum_of_completed_attempt_seconds=math.fsum(
            r["attempt_elapsed_seconds"] for r in completed
        ) if completed else None,
        **{
            name: _distribution(
                [r["telemetry"]["elapsed_seconds"][name] for r in present]
            )
            for name in (*WORKER_INTERVALS, "launcher_container_seconds")
        },
    )
    states = {s: [r["case_id"] for r in rows if r["state"] == s] for s in STATES}
    storage_complete = len(completed) == len(rows)
    memory_complete = storage_complete and not missing_telemetry
    return dict(
        states=states,
        storage_complete=storage_complete,
        memory_complete=memory_complete,
        completed_cases_subtotal=dict(subtotal, cases=len(completed)),
        memory=memory,
        timing=timing,
    )


def projections():
    """Derived, separately named figures from the retained publications."""
    root = repository_root()
    smoke_bytes = (root / "sim/reference/trace-artifact-smoke.json").read_bytes()
    capture_bytes = (root / "sim/reference/trace-capture.json").read_bytes()
    smoke = loads(smoke_bytes)
    capture = loads(capture_bytes)
    per_case = smoke["storage"]["companion_surface"]["per_case"]
    values = sorted({v["trace_payload_bytes"] for v in per_case.values()})
    require(len(values) == 1, "smoke per-case trace payloads disagree")
    return dict(
        label="projection: arithmetic from bounded publications, not a measurement",
        smoke_trace_payload_bytes_times_96=dict(
            source="sim/reference/trace-artifact-smoke.json",
            source_sha256=digest(smoke_bytes),
            per_case_trace_payload_bytes=values[0],
            cases=96,
            projected_trace_payload_bytes=values[0] * 96,
        ),
        capture_projection_96_cases_selected_bytes=dict(
            source="sim/reference/trace-capture.json",
            source_sha256=digest(capture_bytes),
            value=capture["measured_costs"]["projection_96_cases_selected_bytes"],
            note="retained-capture projection from #23; a different quantity"
            " from stored trace payloads",
        ),
    )


LIMITS = [
    "Development identities 0-95 only; no holdout identity was selected,"
    " admitted, rendered or inspected.",
    "The three normalization seams (mixer.pre_normalization, mixer.peak,"
    " mixer.gain) are receipt-retained by the landed worker's boundary"
    " profiler; they are not stored trace files and are not in the trace"
    " payload totals.",
    "Logical bytes are exact file lengths of the verified store; allocated"
    " bytes are filesystem-dependent st_blocks*512 and are reported, not"
    " verified.",
    "Retained #23 capture bytes and stored #24 trace payloads are different"
    " quantities; projections are labelled and never substituted for"
    " measurement.",
    "Per-case RSS is a single worker process's peak; values are not summed"
    " and do not describe simultaneous corpus memory.",
    "Bounded software evidence only: no fidelity, scalar, synthesis, layout,"
    " signoff or hardware playback claim.",
]


def unrun_rows(plan):
    return [
        dict(
            case_id=cid,
            sound_index=index,
            state="unrun",
            render_attempts=0,
            artifact=None,
            failure="campaign-not-executed",
            storage=None,
            telemetry=None,
            attempt_elapsed_seconds=None,
            normalization_seams=None,
        )
        for cid, index in zip(plan["case_ids"], plan["indices"])
    ]


def unrun_receipt(*, plan, execution):
    """Honest UNRUN record: frozen plan, refusal, no measurement."""
    require(execution["outcome"] == "unrun", "unrun receipt needs an unrun outcome")
    rows = unrun_rows(plan)
    return dict(
        schema=SCHEMA,
        schema_version=SCHEMA_VERSION,
        status="unrun",
        plan=plan,
        execution=execution,
        cases=rows,
        run=None,
        storage=None,
        memory=None,
        timing=None,
        completeness=dict(
            storage_complete=False,
            memory_complete=False,
            states={s: [r["case_id"] for r in rows if r["state"] == s] for s in STATES},
        ),
        projections=projections(),
        limits=LIMITS
        + ["UNRUN: no render, store or measurement exists for this receipt;"
           " it is not a pass and does not establish #7 criterion 8."],
    )


def measured_receipt(*, plan, plan_reference, envelope, run_reference, index,
                     companion, root, execution):
    """Measured receipt recomputed from the verified store and run journal."""
    store = ReadOnlyStore(root)
    rows, allocated = case_rows(
        plan, index, envelope["attempts"], companion, store=store, root=root
    )
    summary = summaries(rows)
    inventory = store_inventory(root)
    status = "complete" if summary["memory_complete"] else "partial"
    subtotal = summary["completed_cases_subtotal"]
    companion_bytes = ta.companion_reference(companion)["size_bytes"]
    held = inventory["categories"]["companion"]
    return dict(
        schema=SCHEMA,
        schema_version=SCHEMA_VERSION,
        status=status,
        plan=plan,
        execution=execution,
        cases=rows,
        run=dict(
            run_id=envelope["run_id"],
            run_summary=run_reference,
            index=envelope["index"],
            counts=envelope["counts"],
            plan_reference=plan_reference,
            companion=ta.companion_reference(companion),
        ),
        storage=dict(
            compression="none",
            completed_cases_subtotal=subtotal,
            full_corpus=subtotal if summary["storage_complete"] else None,
            shared=dict(
                companion_bytes=companion_bytes,
                superseded_companion_files=held["files"] - 1,
                superseded_companion_bytes=held["logical_bytes"] - companion_bytes,
                accounting="the current companion is one shared file; it is counted"
                " once here and never apportioned to cases. Companions retained"
                " from earlier partial publications of a resumed run are never"
                " deleted; their files and bytes are reported separately as"
                " superseded and still counted in the store inventory",
            ),
            store_inventory=logical_inventory(inventory),
            allocated=dict(
                note="filesystem-dependent st_blocks*512 at measurement time;"
                " informational, not verified",
                total_allocated_bytes=inventory["total_allocated_bytes"],
                categories={
                    k: v["allocated_bytes"] for k, v in inventory["categories"].items()
                },
                per_case=allocated,
            ),
        ),
        memory=summary["memory"],
        timing=summary["timing"],
        completeness=dict(
            storage_complete=summary["storage_complete"],
            memory_complete=summary["memory_complete"],
            states=summary["states"],
        ),
        projections=projections(),
        limits=LIMITS
        + (
            []
            if status == "complete"
            else ["PARTIAL: measured subtotals cover completed cases only and are"
                  " not full-corpus costs; failed/unrun/missing-telemetry cases"
                  " are listed."]
        ),
    )


# --------------------------------------------------------------------------
# Verification

UNRUN, LIMITED, VERIFIED = "UNRUN", "LIMITED", "VERIFIED"


def _check_rows(receipt):
    plan = receipt["plan"]
    rows = receipt["cases"]
    require(type(rows) is list, "cases must be a list")
    ids = [r.get("case_id") for r in rows]
    require(len(ids) == len(set(ids)), "duplicate case row")
    unexpected = sorted(set(ids) - set(plan["case_ids"]), key=str)
    missing = sorted(set(plan["case_ids"]) - set(ids), key=str)
    require(not unexpected, "unexpected case rows: " + ", ".join(map(str, unexpected)))
    require(not missing, "missing case rows: " + ", ".join(missing))
    require(ids == plan["case_ids"], "case rows are not in plan order")
    for row, index in zip(rows, plan["indices"]):
        require(row["sound_index"] == index, "row identity mismatch: " + row["case_id"])
        require(row["state"] in STATES, "unknown case state: " + row["case_id"])
        if row["state"] == "completed":
            require(row["artifact"] is not None and row["failure"] is None
                    and row["storage"] is not None and row["telemetry"] is not None
                    and _finite(row["attempt_elapsed_seconds"]),
                    "completed row lacks measurements: " + row["case_id"])
            storage = row["storage"]
            require(set(storage) == set(_CASE_STORAGE_KEYS)
                    and all(type(storage[k]) is int and storage[k] >= 0
                            for k in _CASE_STORAGE_KEYS),
                    "row storage fields invalid: " + row["case_id"])
            require(
                storage["artifact_logical_bytes"]
                == storage["trace_payload_bytes"] + storage["audio_bytes"]
                + storage["artifact_metadata_bytes"]
                and storage["case_logical_bytes"]
                == storage["artifact_logical_bytes"] + storage["bundle_bytes"]
                and storage["trace_payload_files"]
                == len(plan["selection"]["requested_traces"]),
                "row storage totals tampered: " + row["case_id"],
            )
            validate_telemetry(row["telemetry"])
            seams = row["normalization_seams"]
            require(seams is not None and seams["state"] == "receipt-retained"
                    and seams["names"] == list(ta.NORMALIZATION_SEAMS),
                    "normalization seams are not receipt-retained: " + row["case_id"])
        else:
            require(row["artifact"] is None and row["storage"] is None
                    and row["telemetry"] is None
                    and row["attempt_elapsed_seconds"] is None
                    and type(row["failure"]) is str and row["failure"],
                    "non-completed row carries measurements or no failure: "
                    + row["case_id"])
    return rows


def verify_document(receipt, *, expected=None):
    """Receipt-only checks. Cannot establish integrity of absent raw bytes."""
    require(type(receipt) is dict and receipt.get("schema") == SCHEMA
            and receipt.get("schema_version") == SCHEMA_VERSION,
            "not a development trace-cost receipt")
    require(receipt["status"] in STATUSES, "unknown receipt status")
    check_plan(receipt["plan"], expected)
    rows = _check_rows(receipt)
    require(receipt["projections"] == projections(),
            "projections disagree with the retained publications")
    states = {s: [r["case_id"] for r in rows if r["state"] == s] for s in STATES}
    if receipt["status"] == "unrun":
        require(receipt["execution"]["outcome"] == "unrun",
                "unrun receipt claims an execution")
        require(all(r["state"] == "unrun" and r["render_attempts"] == 0 for r in rows),
                "unrun receipt carries case results")
        require(receipt["run"] is None and receipt["storage"] is None
                and receipt["memory"] is None and receipt["timing"] is None,
                "unrun receipt carries measurements")
        require(receipt["completeness"] == dict(
            storage_complete=False, memory_complete=False, states=states),
            "unrun completeness tampered")
        return UNRUN
    require(receipt["execution"]["outcome"] == "executed",
            "measured receipt lacks an executed outcome")
    summary = summaries(rows)
    require(receipt["completeness"] == dict(
        storage_complete=summary["storage_complete"],
        memory_complete=summary["memory_complete"],
        states=summary["states"]), "completeness tampered")
    require(receipt["status"]
            == ("complete" if summary["memory_complete"] else "partial"),
            "status disagrees with completeness")
    storage = receipt["storage"]
    require(storage["completed_cases_subtotal"]
            == summary["completed_cases_subtotal"], "storage subtotals tampered")
    require(storage["full_corpus"]
            == (summary["completed_cases_subtotal"]
                if summary["storage_complete"] else None),
            "full-corpus total claimed for an incomplete run")
    require(receipt["memory"] == summary["memory"], "memory summary tampered")
    require(receipt["timing"] == summary["timing"], "timing summary tampered")
    categories = storage["store_inventory"]["categories"]
    subtotal = summary["completed_cases_subtotal"]
    for category, key in (("trace_payload", "trace_payload_bytes"),
                          ("audio", "audio_bytes"),
                          ("artifact_metadata", "artifact_metadata_bytes"),
                          ("bundle", "bundle_bytes")):
        require(categories[category]["logical_bytes"] == subtotal[key],
                "store inventory disagrees with case rows: " + category)
    shared = storage["shared"]
    require(shared["superseded_companion_files"] >= 0
            and shared["superseded_companion_bytes"] >= 0
            and (shared["superseded_companion_files"] == 0)
            == (shared["superseded_companion_bytes"] == 0),
            "superseded companion accounting is inconsistent")
    require(categories["companion"]
            == dict(files=1 + shared["superseded_companion_files"],
                    logical_bytes=shared["companion_bytes"]
                    + shared["superseded_companion_bytes"]),
            "companions are not one current file plus accounted superseded files")
    require(storage["store_inventory"]["total_logical_bytes"]
            == sum(v["logical_bytes"] for v in categories.values()),
            "store logical total tampered")
    require(storage["store_inventory"]["artifact_ids"]
            == sorted(r["artifact"]["artifact_id"] for r in rows
                      if r["state"] == "completed"),
            "store artifacts differ from completed cases")
    return LIMITED


def _strip_allocated(receipt):
    value = dict(receipt)
    value["storage"] = dict(receipt["storage"], allocated=None)
    return value


def verify_store(receipt, store_root, *, expected=None):
    """Rehash the retained raw store and recompute the whole receipt."""
    state = verify_document(receipt, expected=expected)
    if state == UNRUN:
        return UNRUN
    root = Path(os.path.abspath(store_root))
    require(root.is_dir(), "raw store is absent: " + str(root))
    run = receipt["run"]
    plan_bytes = read_reference(root, run["plan_reference"])
    require(loads(plan_bytes) == receipt["plan"]
            and run["plan_reference"]["ref"] == PLAN_STORE_REF,
            "frozen plan in the store differs from the receipt")
    envelope = verify_run(root, run["run_id"],
                          expected_sha256=run["run_summary"]["sha256"])
    corpus_plan = loads(read_reference(root, envelope["plan"]))
    require(
        [c["sound_index"] for c in corpus_plan["selection"]] == receipt["plan"]["indices"]
        and digest(canonical_bytes(corpus_plan["template"]))
        == receipt["plan"]["template_sha256"]
        and corpus_plan["manifest"]["sha256"] == receipt["plan"]["manifest"]["sha256"],
        "corpus run is not the frozen plan",
    )
    index = loads(read_reference(root, envelope["index"]))
    companion = loads(read_reference(root, run["companion"]))
    store = ReadOnlyStore(root)
    stats = ta.validate_companion(companion, root=root, store=store)
    recomputed = measured_receipt(
        plan=receipt["plan"],
        plan_reference=run["plan_reference"],
        envelope=envelope,
        run_reference=run["run_summary"],
        index=index,
        companion=companion,
        root=root,
        execution=receipt["execution"],
    )
    require(_strip_allocated(recomputed) == _strip_allocated(receipt),
            "receipt differs from the recomputed store measurement")
    subtotal = receipt["storage"]["completed_cases_subtotal"]
    require(
        stats["bundles"] == subtotal["cases"]
        and stats.get("trace_bytes", 0) == subtotal["trace_payload_bytes"],
        "rehashed trace bytes disagree with the receipt",
    )
    return VERIFIED
