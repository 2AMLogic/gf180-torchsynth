"""Scoped fault-injection runtime bridge for the qualification apparatus.

The #30 execution side of spec/MUTATIONS.md. Stdlib-only: the apparatus bridge
wraps apparatus-owned callables declared in the seam catalog, never producer
or render-path modules, and it removes every wrapper after success, operator
errors and partial setup failure. The voice-runtime side of this module is
resolution-only stdlib code: it validates declared ``voice.*`` mutations
against the landed #22 registry/#23 capture contract and produces the JSON
execution schedule consumed by the pinned release-era worker
(``env/release-era/mutation_worker.py``), which owns the actual Torch
mechanics inside the qualified runtime. Actual-Voice evidence is bounded and
executed under the DR-0006 gated tool; it is never substituted by synthetic
proofs, and unavailable runtime work is recorded not-run, never a pass.

In-band reporting: every application is recorded as an ordered event
(``applied``/``ineffective``/``errored``/``refused``); an exception raised at
a seam is recorded as an ``errored`` event naming the error type and then
re-raised. Completion is fail-closed: missing, extra, duplicate or reordered
events refuse the attempt, and a fault that never ran is never evidence.

Downstream refusal checks call only landed validators (scorecard, artifacts,
trace_capture, case_registry); each injected fault must demonstrably trip the
named refusal, and the clean control must demonstrably pass the same check.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import mutations
from .mutations import MutationError


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise MutationError(reason)


class ProducerCrash(RuntimeError):
    """The identified injected fault type raised at the producer call seam."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def attempt_artifact(case_id: str, size: int = 256) -> bytes:
    """Deterministic pseudo-payload bytes for a case; no RNG is consumed."""
    blocks: List[bytes] = []
    counter = 0
    while sum(len(block) for block in blocks) < size:
        blocks.append(hashlib.sha256((case_id + ":" + str(counter)).encode()).digest())
        counter += 1
    return b"".join(blocks)[:size]


def _apply_operator(instance: Dict[str, Any], value: Any) -> Tuple[Any, Dict[str, Any]]:
    operator_id = instance["operator"]
    magnitude = instance["magnitude"]
    configuration = instance["configuration"]
    if operator_id == "noop.sham":
        return value, {"note": "sham returns the original value"}
    if operator_id == "crash.producer":
        raise ProducerCrash("injected producer crash at " + instance["seam"])
    if operator_id == "truncate.payload":
        kept = magnitude["value"]
        if not type(value) is bytes or kept >= len(value):
            return value, {"reason": "out of applicability"}
        return value[:kept], {"kept_bytes": kept, "original_size": len(value)}
    if operator_id == "corrupt.byte":
        offset = magnitude["value"]
        if not type(value) is bytes or offset >= len(value):
            return value, {"reason": "out of applicability"}
        corrupted = value[:offset] + bytes([value[offset] ^ 0x01]) + value[offset + 1 :]
        return corrupted, {"offset": offset}
    if operator_id == "corrupt.digest":
        row = copy.deepcopy(value)
        row["artifact"]["sha256"] = sha256(b"torchsynth-corrupt-digest-v1")
        return row, {"rebound_to": row["artifact"]["sha256"]}
    if operator_id == "drop.receipt":
        return None, {"dropped": magnitude["value"]}
    if operator_id == "swap.capture":
        left, right = configuration["left"], configuration["right"]
        swapped = copy.deepcopy(value)
        names = [entry["name"] for entry in swapped]
        if left not in names or right not in names:
            return value, {"reason": "selector did not match"}
        first, second = names.index(left), names.index(right)
        swapped[first]["name"], swapped[second]["name"] = (
            swapped[second]["name"],
            swapped[first]["name"],
        )
        return swapped, {"swapped": [left, right]}
    if operator_id == "spoof.partition":
        claim = configuration["claim"]
        return claim, {"claim": claim}
    raise MutationError("operator has no runtime application: " + operator_id)


class InjectionSession:
    """Wrap declared seam targets for one attempt; always restore on exit.

    Mutations apply at a seam in the declared plan order. The original target
    runs first with its original arguments; each declared instance then sees
    the current value. Sham instances traverse dispatch and return the value
    unchanged. Errors are recorded in-band and re-raised.
    """

    def __init__(self, plan: Dict[str, Any], targets: Dict[str, Callable[..., Any]]):
        self.plan = plan
        self.targets = targets
        self._events: List[Dict[str, Any]] = []
        self._active = False
        self._originals: Dict[str, Callable[..., Any]] = {}
        self._by_seam: Dict[str, List[Dict[str, Any]]] = {}
        for instance in plan["mutations"]:
            self._by_seam.setdefault(instance["seam"], []).append(instance)

    def __enter__(self) -> "InjectionSession":
        _require(not self._active, "injection session already active")
        for seam in self._by_seam:
            _require(
                seam in self.targets and callable(self.targets[seam]),
                "declared seam has no target: " + seam,
            )
        self._active = True
        try:
            self._originals = dict(self.targets)
            for seam in self._by_seam:
                self.targets[seam] = self._wrap(seam, self.targets[seam])
        except BaseException:
            self._restore()
            raise
        return self

    def _wrap(self, seam: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def dispatched(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            for instance in self._by_seam[seam]:
                result = self._apply(seam, instance, result)
            return result

        return dispatched

    def _apply(self, seam: str, instance: Dict[str, Any], value: Any) -> Any:
        try:
            replacement, detail = _apply_operator(instance, value)
        except BaseException as error:
            self._record(
                instance,
                seam,
                "errored",
                {"error": type(error).__name__, "message": str(error)},
            )
            raise
        if replacement == value and type(replacement) is type(value):
            self._record(
                instance,
                seam,
                "ineffective",
                detail if detail else {"reason": "replacement equals original"},
            )
            return value
        self._record(instance, seam, "applied", detail)
        return replacement

    def _record(self, instance: Dict[str, Any], seam: str, status: str, detail: Dict[str, Any]) -> None:
        for event in self._events:
            _require(
                event["instance_id"] != instance["instance_id"],
                "duplicate event for instance: " + instance["instance_id"],
            )
        self._events.append(
            {
                "instance_id": instance["instance_id"],
                "seam": seam,
                "status": status,
                "order": len(self._events),
                "sham": instance["sham"],
                "detail": detail,
            }
        )

    def _restore(self) -> None:
        self.targets.clear()
        self.targets.update(self._originals)
        self._active = False

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self._restore()
        if exc_type is None:
            self.finish()
        return False

    @property
    def events(self) -> List[Dict[str, Any]]:
        return [dict(event) for event in self._events]

    def finish(self) -> Dict[str, Any]:
        """Fail-closed completeness gate over the in-band event log."""
        return mutations.validate_events(self.plan, self._events)


RUBRIC = {"id": "mutation-harness", "version": "1"}
ESTIMATOR = {"name": "mutation-attempt", "version": "1"}
EXPECTED_INVENTORY = {"receipts": 1, "artifacts": 1}
CAPTURE_SELECTION = ["adsr_1.output", "mixer.peak"]
PAYLOAD_SIZE = 256


def receipt_row(
    case_id: str,
    partition: str,
    artifact_identity: str,
    artifact_sha256: str,
    observed_size: int,
) -> Dict[str, Any]:
    """A landed-scorecard-shaped attempt receipt; validate_row owns its truth."""
    return {
        "schema_version": 1,
        "case": {"id": case_id, "partition": partition},
        "trace": "mutation-attempt-payload",
        "property": "payload_size",
        "estimator": dict(ESTIMATOR),
        "rubric": dict(RUBRIC),
        "unit": "byte",
        "expected": {"value": PAYLOAD_SIZE, "source": "mutation harness fixture definition"},
        "observed": observed_size,
        "tolerance": {"value": 0, "source": "mutation harness fixture definition"},
        "validity": {"status": "valid", "reason": "attempt receipt under the harness rubric"},
        "coverage": "complete",
        "verdict": "PASS",
        "artifact": {"identity": artifact_identity, "sha256": artifact_sha256},
    }


def capture_inventory(
    document: Dict[str, Any],
    names: Optional[List[str]] = None,
    batch_size: int = 32,
) -> List[Dict[str, Any]]:
    """Graph-ordered capture descriptors matching the #22 registry selection.

    Digests are the registry-neutral placeholder used by the landed capture
    negative controls; the capture validator owns name/shape/rate truth.
    """
    from . import trace_capture

    plan = trace_capture.requested_plan(document, names)
    return [
        {
            "name": trace["name"],
            "shape": list(trace["shape"]),
            "batch_shape": [batch_size if n == "B" else n for n in trace["batch_shape"]],
            "dtype": trace["dtype"],
            "rate_hz": trace["rate_hz"],
            "boundary": trace["boundary"],
            "sha256": "0" * 64,
        }
        for trace in plan
    ]


class AttemptResult:
    """One bounded attempt: store bytes, receipts, events and completeness."""

    def __init__(self) -> None:
        self.store: Dict[str, bytes] = {}
        self.receipts: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.events_summary: Dict[str, Any] = {}
        self.errored: Optional[str] = None
        self.binding: Dict[str, Any] = {}
        self.partition_claim: Optional[str] = None
        self.inventory: List[Dict[str, Any]] = []

    @property
    def payload(self) -> bytes:
        return self.store["attempt/payload.bin"]

    @property
    def inventory_observed(self) -> Dict[str, int]:
        return {"receipts": len(self.receipts), "artifacts": len(self.store)}


class MutationHarness:
    """Deterministic apparatus fixture producer; the injection target owner.

    Seams are ordinary callables this harness owns. The evidence digest step
    is deliberately absent from the target set: the catalog marks it
    non-writable, so declaring a mutation there must refuse at plan time.
    """

    CASE_ID = "directed:mutation-harness-0"
    PARTITION = "development"

    def __init__(self, root: Path, document: Optional[Dict[str, Any]] = None):
        self.root = Path(root)
        self.document = document
        self.catalog = mutations.load_seam_catalog(self.root / mutations.SEAM_CATALOG_PATH)

    def binding(self) -> Dict[str, Any]:
        return {
            "case_id": self.CASE_ID,
            "partition": self.PARTITION,
            "fixture_identity": sha256(attempt_artifact(self.CASE_ID, PAYLOAD_SIZE)),
        }

    def _produce(self) -> bytes:
        return attempt_artifact(self.CASE_ID, PAYLOAD_SIZE)

    @staticmethod
    def _store_ingress(payload: bytes) -> bytes:
        """Storage ingress seam: returns exactly the bytes that get persisted."""
        return payload

    @staticmethod
    def _receipt_dispatch(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Receipt emission seam: returns the row to append, or None to drop."""
        return row

    @staticmethod
    def _partition() -> str:
        return MutationHarness.PARTITION

    def _inventory(self) -> List[Dict[str, Any]]:
        return capture_inventory(self.document, names=CAPTURE_SELECTION)

    def _targets(self) -> Dict[str, Callable[..., Any]]:
        targets: Dict[str, Callable[..., Any]] = {
            "apparatus.producer_call": self._produce,
            "apparatus.artifact_write": self._store_ingress,
            "apparatus.receipt_append": self._receipt_dispatch,
            "board.partition_access": self._partition,
        }
        if self.document is not None:
            targets["capture.inventory"] = self._inventory
        return targets

    def plain_attempt(self) -> AttemptResult:
        """Ordinary baseline control: no session installed, no targets wrapped."""
        result = AttemptResult()
        payload = self._produce()
        stored = self._store_ingress(payload)
        result.store["attempt/payload.bin"] = stored
        result.receipts.append(
            receipt_row(
                self.CASE_ID,
                self.PARTITION,
                mutations.attempt_artifact_identity(payload),
                sha256(payload),
                len(payload),
            )
        )
        result.partition_claim = self._partition()
        if self.document is not None:
            result.inventory = self._inventory()
        return result

    def attempt(self, plan: Dict[str, Any]) -> AttemptResult:
        """Run one declared-plan attempt with in-band events and cleanup.

        The receipt always binds the producer's declared payload bytes; the
        write seam decides what is actually persisted, so a diverging store is
        detectable downstream instead of silently rebinding the receipt.
        """
        result = AttemptResult()
        session = InjectionSession(plan, self._targets())
        try:
            with session:
                payload = session.targets["apparatus.producer_call"]()
                declared_sha256 = sha256(payload)
                stored = session.targets["apparatus.artifact_write"](payload)
                result.store["attempt/payload.bin"] = stored
                row = receipt_row(
                    plan["source_binding"]["case_id"],
                    plan["source_binding"]["partition"],
                    mutations.attempt_artifact_identity(payload),
                    declared_sha256,
                    len(payload),
                )
                appended = session.targets["apparatus.receipt_append"](row)
                if appended is not None:
                    result.receipts.append(appended)
                result.partition_claim = session.targets["board.partition_access"]()
                if self.document is not None:
                    result.inventory = session.targets["capture.inventory"]()
        except BaseException as error:
            result.errored = type(error).__name__ + ": " + str(error)
        result.events = session.events
        result.events_summary = session.finish()
        return result

    def empty_plan(self) -> Dict[str, Any]:
        return mutations.make_plan(self.binding(), [], self.catalog)

    def sham_plan(self) -> Dict[str, Any]:
        return mutations.make_plan(
            self.binding(),
            [instance("mi-sham", "noop.sham", "apparatus.producer_call")],
            self.catalog,
        )


def instance(
    instance_id: str,
    operator_id: str,
    seam: str,
    magnitude: Any = None,
    configuration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build one mutation instance bound to the operator registration."""
    definition = mutations.OPERATORS[operator_id]
    magnitude_value = definition["magnitude"]
    return {
        "instance_id": instance_id,
        "operator": operator_id,
        "operator_version": definition["version"],
        "seam": seam,
        "magnitude": None
        if magnitude_value["type"] == "null"
        else {"value": magnitude, "unit": magnitude_value["unit"]},
        "configuration": dict(configuration or {}),
        "sham": bool(definition["sham"]),
    }


def _plan_for(harness: MutationHarness, *instances: Dict[str, Any]) -> Dict[str, Any]:
    return mutations.make_plan(harness.binding(), list(instances), harness.catalog)


def fault_matrix(document: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Run every fault and require its landed downstream refusal to trip.

    Each entry pairs a clean control (the same downstream check must accept
    the unfaulted attempt) with a faulted attempt (the check must refuse with
    the expected reason). A fault whose downstream consumer still accepts is
    reported as not tripped and fails the qualification; it is never counted
    as detection.
    """
    from . import artifacts, case_registry
    from . import trace_capture

    harness = MutationHarness(Path(__file__).resolve().parents[2], document)
    clean = harness.plain_attempt()
    clean_receipt = clean.receipts[0]
    entries: List[Dict[str, Any]] = []

    def record(
        fault: str,
        operator: str,
        seam: str,
        downstream: str,
        expected: str,
        control_ok: bool,
        observed: Optional[str],
    ) -> None:
        entries.append(
            {
                "fault": fault,
                "operator": operator,
                "seam": seam,
                "downstream": downstream,
                "expected_refusal": expected,
                "observed_refusal": observed,
                "tripped": bool(control_ok and observed is not None and expected in observed),
                "control_accepted": bool(control_ok),
            }
        )

    def refusal_of(plan: Dict[str, Any], check: Callable[[AttemptResult], None]) -> Optional[str]:
        try:
            check(harness.attempt(plan))
        except (ValueError, OSError) as error:
            return str(error)
        return None

    def verify(result: AttemptResult) -> None:
        artifacts.verify_sha256(result.payload, result.receipts[0]["artifact"]["sha256"])

    control_verify_ok = True
    try:
        verify(clean)
    except (ValueError, OSError):
        control_verify_ok = False

    partial = _plan_for(harness, instance("mi-partial", "truncate.payload", "apparatus.artifact_write", 100))
    record(
        "partial-write",
        "truncate.payload",
        "apparatus.artifact_write",
        "artifacts.verify_sha256",
        "file SHA-256 mismatch",
        control_verify_ok,
        refusal_of(partial, verify),
    )

    corrupt = _plan_for(harness, instance("mi-corrupt", "corrupt.byte", "apparatus.artifact_write", 7))
    record(
        "corrupt-artifact",
        "corrupt.byte",
        "apparatus.artifact_write",
        "artifacts.verify_sha256",
        "file SHA-256 mismatch",
        control_verify_ok,
        refusal_of(corrupt, verify),
    )

    rebind = _plan_for(harness, instance("mi-rebind", "corrupt.digest", "apparatus.receipt_append"))
    record(
        "corrupt-receipt",
        "corrupt.digest",
        "apparatus.receipt_append",
        "artifacts.verify_sha256",
        "file SHA-256 mismatch",
        control_verify_ok,
        refusal_of(rebind, verify),
    )

    def denominator(result: AttemptResult) -> None:
        mutations.require_complete_inventory(EXPECTED_INVENTORY, result.inventory_observed)

    control_denominator_ok = True
    try:
        denominator(clean)
    except ValueError:
        control_denominator_ok = False

    dropped = _plan_for(harness, instance("mi-drop", "drop.receipt", "apparatus.receipt_append", 1))
    record(
        "dropped-receipt",
        "drop.receipt",
        "apparatus.receipt_append",
        "mutations.require_complete_inventory",
        "incomplete attempt inventory for receipts",
        control_denominator_ok,
        refusal_of(dropped, denominator),
    )

    crash = _plan_for(harness, instance("mi-crash", "crash.producer", "apparatus.producer_call"))

    control_outcome_ok = case_registry.row_outcome(clean.receipts) == "PASS"
    crashed_attempt = harness.attempt(crash)
    crashed_reported = (
        crashed_attempt.errored is not None
        and crashed_attempt.errored.startswith("ProducerCrash")
        and bool(crashed_attempt.events)
        and crashed_attempt.events[0]["status"] == "errored"
        and crashed_attempt.events[0]["detail"]["error"] == "ProducerCrash"
    )
    crashed_outcome = case_registry.row_outcome(crashed_attempt.receipts)
    record(
        "producer-crash",
        "crash.producer",
        "apparatus.producer_call",
        "case_registry.row_outcome",
        "NO VERDICT",
        control_outcome_ok,
        crashed_outcome
        if crashed_reported and crashed_outcome == "NO VERDICT"
        else "crashed attempt did not aggregate to the NO VERDICT refusal state",
    )

    if document is not None:
        swapped = _plan_for(
            harness,
            instance(
                "mi-swap",
                "swap.capture",
                "capture.inventory",
                configuration={"left": "adsr_1.output", "right": "mixer.peak"},
            ),
        )

        def captures(result: AttemptResult) -> None:
            trace_capture.validate_selected_capture(document, CAPTURE_SELECTION, result.inventory, 32)

        control_capture_ok = True
        try:
            captures(clean)
        except ValueError:
            control_capture_ok = False
        record(
            "swapped-capture",
            "swap.capture",
            "capture.inventory",
            "trace_capture.validate_selected_capture",
            "capture inventory mismatch",
            control_capture_ok,
            refusal_of(swapped, captures),
        )

    absent_root = Path("/nonexistent/gf180-mutation-gate")
    spoof_registry = {"id": "mutation-gate-proof"}

    def gate(result: AttemptResult) -> None:
        claim = result.partition_claim
        case_registry.evaluate(
            spoof_registry,
            absent_root,
            partition=claim,
            holdout_authorization=None,
        )

    control_gate_ok = True
    try:
        case_registry.evaluate(spoof_registry, absent_root, partition="development")
    except case_registry.RegistryError as error:
        control_gate_ok = "holdout refused" not in str(error)

    spoofed = _plan_for(
        harness,
        instance("mi-spoof", "spoof.partition", "board.partition_access", configuration={"claim": "holdout"}),
    )
    record(
        "spoofed-partition",
        "spoof.partition",
        "board.partition_access",
        "case_registry.evaluate holdout gate",
        "holdout refused",
        control_gate_ok,
        refusal_of(spoofed, gate),
    )
    return entries


def qualification_controls(document: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Ordinary/empty/sham controls, RNG isolation and cleanup guarantees."""
    import random

    harness = MutationHarness(Path(__file__).resolve().parents[2], document)
    plain = harness.plain_attempt()
    empty = harness.attempt(harness.empty_plan())
    sham = harness.attempt(harness.sham_plan())
    rng_before = random.getstate()
    harness.attempt(harness.sham_plan())
    plain_again = harness.plain_attempt()
    rng_untouched = random.getstate() == rng_before

    restore_targets: Dict[str, Callable[..., Any]] = {
        "apparatus.producer_call": harness._produce,
    }
    crash_plan = _plan_for(harness, instance("mi-crash", "crash.producer", "apparatus.producer_call"))
    session = InjectionSession(crash_plan, restore_targets)
    crashed = False
    try:
        with session:
            restore_targets["apparatus.producer_call"]()
    except ProducerCrash:
        crashed = True
    clean_after_crash = restore_targets["apparatus.producer_call"] == harness._produce

    def stored(receipts: List[Dict[str, Any]]) -> List[Any]:
        return [receipt["artifact"]["sha256"] for receipt in receipts]

    return {
        "plain_vs_empty_plan_bytes_identical": plain.store == empty.store
        and stored(plain.receipts) == stored(empty.receipts),
        "plain_vs_sham_bytes_identical": plain.store == sham.store
        and stored(plain.receipts) == stored(sham.receipts),
        "plain_rerun_deterministic": plain.store == plain_again.store
        and stored(plain.receipts) == stored(plain_again.receipts),
        "empty_plan_events_complete": empty.events_summary.get("complete") is True
        and empty.events_summary.get("observed") == 0,
        "sham_events_marked_and_ineffective": bool(sham.events)
        and all(event["sham"] for event in sham.events)
        and all(event["status"] == "ineffective" for event in sham.events),
        "sham_plan_identity_distinct": harness.sham_plan()["plan_id"]
        != harness.empty_plan()["plan_id"],
        "attempt_artifact_identity_distinct_from_render_identity": mutations.attempt_artifact_identity(
            plain.payload
        ).startswith("mu1-"),
        "global_rng_untouched_by_attempts": rng_untouched,
        "cleanup_after_producer_crash": crashed and clean_after_crash,
        "no_errored_events_in_controls": plain.errored is None
        and empty.errored is None
        and sham.errored is None,
    }


VOICE_POST_MODULE_SEAM = "voice.post_module"
VOICE_PARAMETER_SEAM = "voice.parameter_value"
SCHEDULE_SCHEMA_VERSION = 1


def module_seam_map(document: Dict[str, Any]) -> Dict[str, Tuple[str, int]]:
    """Registry-derived replacement targets: trace name -> (module, occurrence).

    Only single-output forward-hook traces are executable replacement seams.
    Profiler-derived observations (``mixer.pre_normalization``/``peak``/``gain``)
    are strictly passive and are absent, as are multi-output hook results. The
    call association is the landed #22 registry's own producer binding, so a
    module-wide hook cannot silently mutate the wrong occurrence of a reused
    module (control VCA 2, control upsample 5, audio VCA 3).
    """
    seam_map: Dict[str, Tuple[str, int]] = {}
    for trace in document["traces"]:
        producer = trace["producer"]
        if (
            producer["mechanism"] == "forward-hook"
            and producer["output_index"] is None
        ):
            seam_map[trace["name"]] = (producer["module"], producer["occurrence"] - 1)
    return seam_map


def resolve_voice_plan(
    plan: Dict[str, Any],
    document: Dict[str, Any],
    parameter_names: List[str],
    batch_size: int,
) -> Dict[str, Any]:
    """Validate declared voice mutations and resolve the execution schedule.

    Fail-closed before execution: unknown trace or parameter names, seams
    whose producer binding is not a writable hook (the normalization decision
    among them), slots outside the pinned batch, and module mutations
    declared before parameter mutations (graph causality) are all refused
    here.     The returned schedule is plain JSON for the pinned release-era
    worker; it binds the plan identity and the declared instance order.
    """
    from . import trace_registry

    trace_registry.validate_registry(document)
    _require(
        type(batch_size) is int and batch_size > 0 and batch_size % 32 == 0,
        "voice attempts require supported reproducible batching",
    )
    _require(
        type(parameter_names) is list
        and len(set(parameter_names)) == len(parameter_names)
        and all(type(name) is str and name.strip() for name in parameter_names),
        "parameter names must be distinct nonblank strings",
    )
    seam_map = module_seam_map(document)
    module_mutations: List[Dict[str, Any]] = []
    parameter_swaps: List[Dict[str, Any]] = []
    declared_order: List[str] = []
    for position, instance_plan in enumerate(plan["mutations"]):
        instance_id = instance_plan["instance_id"]
        declared_order.append(instance_id)
        configuration = instance_plan["configuration"]
        seam = instance_plan["seam"]
        if seam == VOICE_POST_MODULE_SEAM:
            trace = configuration["trace"]
            _require(trace in seam_map, "unknown module-output trace: " + str(trace))
            module, occurrence = seam_map[trace]
            slot = configuration["slot"]
            _require(slot < batch_size, "slot outside pinned batch: " + instance_id)
            raising = instance_plan["operator"] == "bridge.raise_slot"
            module_mutations.append(
                {
                    "instance_id": instance_id,
                    "trace": trace,
                    "module": module,
                    "occurrence": occurrence,
                    "slot": slot,
                    "ratio": None
                    if raising or instance_plan["magnitude"] is None
                    else instance_plan["magnitude"]["value"],
                    "raise": raising,
                    "sham": instance_plan["sham"],
                    "declared_position": position,
                }
            )
        else:
            _require(
                seam == VOICE_PARAMETER_SEAM,
                "seam has no voice runtime target: " + str(seam),
            )
            parameter = configuration["parameter"]
            _require(
                parameter in parameter_names,
                "unknown inventory parameter: " + str(parameter),
            )
            slot = configuration["slot"]
            _require(slot < batch_size, "slot outside pinned batch: " + instance_id)
            parameter_swaps.append(
                {
                    "instance_id": instance_id,
                    "parameter": parameter,
                    "slot": slot,
                    "ratio": instance_plan["magnitude"]["value"],
                    "sham": instance_plan["sham"],
                    "declared_position": position,
                }
            )
    _require(
        max(
            (swap["declared_position"] for swap in parameter_swaps), default=-1
        )
        < min(
            (mutation["declared_position"] for mutation in module_mutations),
            default=len(declared_order),
        ),
        "parameter mutations must be declared before module mutations (graph causality)",
    )
    return {
        "schema_version": SCHEDULE_SCHEMA_VERSION,
        "kind": "mutation-voice-schedule-v1",
        "plan_id": plan["plan_id"],
        "source_binding": dict(plan["source_binding"]),
        "batch_size": batch_size,
        "module_mutations": module_mutations,
        "parameter_swaps": parameter_swaps,
        "declared_order": declared_order,
    }
