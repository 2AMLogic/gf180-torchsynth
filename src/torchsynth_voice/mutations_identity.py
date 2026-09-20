"""Identity, parameter and noise negative controls (#31).

The #31 fault-operator family of spec/MUTATIONS.md, registered through the
landed #30 public API (``mutations.register_operator``) from this
family-owned file. The landed framework files (``mutations.py``,
``mutation_runtime.py``, the seam catalog) stay untouched: the family adds
operator definitions, a deterministic resolved-request-shaped fixture,
applicators, a harness over the landed ``InjectionSession`` contract, and
detector bindings to the landed #41/#26 identity and conversion contracts
(``identity.SoundIdentity``, ``float_interfaces.ResolvedRequest``,
``directed.physical_value``) plus the landed #28 noise detectors
(``spectral_estimators.noise_identity``/``noise_statistics``).

Every operator binds the declared writable seam ``apparatus.producer_call``,
where the family harness produces a deterministic, development-only fixture
carrying the 78 canonical inventory parameters (normalized binary32-exact
values with the pinned physical conversion), the ``SoundIdentity``
projection of the case's global sound index, and the canonical noise slot
slice. Applicators are explicit trusted code keyed by operator id; they
mutate only the presented fixture fields. The fixture keeps the pristine
declared truth beside the presented values' consumers, so each detector
recomputes from the landed contract and compares against what the producer
presented.

Detector ordering is a contract, not a convention: the provenance rows
(``identity.provenance_match``, ``identity.train_test_match``) and the
parameter conversion row (``parameter.first_wrong_position``) run first and
a failure there refuses before any perceptual row is computed — the gate
and its reason are recorded per case. Wrong-slot and wrong-seed noise must
fail the exact ``noise.identity`` record comparison (bound 0.0) while the
first/second-order noise statistics stay inside the declared plausibility
band; those plausible rows are preserved as receipts, never hidden, because
they are exactly the false positives a statistical detector would raise.
The blast-radius check recomputes the derived lanes from the presented
document and requires the diverged set to equal the operator's declared
expected set; sibling lanes stay byte-identical.

This module never renders the Voice graph, never touches holdout
partitions, consumes no randomness (representative cases are fixed
constants, not samples), and ratifies no numeric format. Actual-Voice
runtime injection of family operators remains DR-0006 gated and is recorded
not-run by the family publication; the landed ``bridge.*`` operators stay
#30-owned test-only proofs and are never published as family qualification.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import directed
from . import float_sources
from . import mutations
from . import mutation_runtime
from . import spectral_estimators
from .float_interfaces import (
    AUDIO_SAMPLES,
    NOISE_SEED,
    NOISE_STREAMS,
    ResolvedRequest,
)
from .identity import SoundIdentity
from .mutations import MutationError
from .paired_metrics import Limit, Rubric, scorecard_rows

FAMILY_SEAM = "apparatus.producer_call"
PAYLOAD_PATH = "attempt/fixture.json"
FIXTURE_SCHEMA = "torchsynth-mutation-identity-fixture-v1"
AUDIO_RATE_HZ = float(directed.FIXTURE_PROTOCOL["sample_rate"])
IDENTITY_BATCH_SIZE = directed.FIXTURE_PROTOCOL["qualification_batch_size"]
SLICE_FULL = AUDIO_SAMPLES
SLICE_REDUCED = 4096

NOISE_LAGS = spectral_estimators.LAGS
PLAUSIBILITY_BAND = 0.05
PLAUSIBILITY_SOURCE = (
    "mutations_identity declared uniform-noise plausibility band "
    "(negative-control witness; not a #28 exact-construction bound)"
)
EXACT_SOURCE = "spectral_estimators.noise_identity exact record comparison (bound 0.0)"
PROVENANCE_SOURCE = (
    "identity.SoundIdentity recomputation at the declared render batch size"
)
CONVERSION_SOURCE = (
    "directed.physical_value pinned-equations-rational-v1 recomputation"
)
BLAST_SOURCE = "mutations_identity declared expected blast-radius set"

CONVERSION_MODES = (
    "linear-where-curved",
    "curved-where-linear",
    "swapped-bounds",
)

DIRECTED_CASES: Dict[str, int] = {
    # 4160: block 4 (train), noise slot 0. 9216: block 9 (test), noise slot 0.
    "directed-train": 4160,
    "directed-test": 9216,
}
REPRESENTATIVE_INDICES: Tuple[int, ...] = (
    17,
    2051,
    3103,
    5125,
    6156,
    8193,
    9244,
    10249,
)

NOISE_TRUTH_LANES = ("noise.source", "noise.vca")
EXPECTED_BLAST: Dict[str, Tuple[str, ...]] = {
    "identity.batch_shift": ("identity.strobe",),
    "identity.train_test_flip": ("identity.strobe",),
    "param.positional_shuffle": ("parameter.control",),
    "param.conversion_substitute": ("parameter.control",),
    "noise.slot_shift": NOISE_TRUTH_LANES,
    "noise.seed_shift": NOISE_TRUTH_LANES,
}

_INVENTORY_ROWS: Optional[Dict[str, Dict[str, Any]]] = None


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise MutationError(reason)


def inventory_rows() -> Dict[str, Dict[str, Any]]:
    """The landed 78-name inventory rows, loaded once per process."""
    global _INVENTORY_ROWS
    if _INVENTORY_ROWS is None:
        _INVENTORY_ROWS = directed.inventory_rows()
    return _INVENTORY_ROWS


def canonical_names() -> List[str]:
    return list(inventory_rows())


def _normalized_value(position: int) -> float:
    """Deterministic binary32-exact normalized value for one canonical position."""
    value = ((position * 7919) % 4096 + 1) / 8192
    decoded = struct.unpack("<f", struct.pack("<f", value))[0]
    _require(decoded == value, "fixture normalized value is not binary32-exact")
    return decoded


def slot_bytes(seed: int, slot: int, sample_count: int) -> bytes:
    """The first ``sample_count`` samples of one pinned noise slot, bit-exact.

    ``float_sources.canonical_noise_slot_bytes`` derives the slot from
    ``sound_index % streams``; passing the slot itself (always < 32) resolves
    exactly that slot's prefix, so truth and faulted selections come from one
    pinned implementation.
    """
    _require(0 <= slot < NOISE_STREAMS, "noise slot outside [0, 32)")
    return float_sources.canonical_noise_slot_bytes(seed, slot, NOISE_STREAMS, sample_count)


def _encode_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_bytes(payload: str) -> bytes:
    return base64.b64decode(payload)


def _lane_digest(samples: List[float]) -> str:
    return hashlib.sha256(struct.pack("<%dd" % len(samples), *samples)).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_fixture(case: str) -> Dict[str, Any]:
    """Deterministic declared fixture: identity, parameters and noise slice."""
    if case in DIRECTED_CASES:
        sound_index = DIRECTED_CASES[case]
        sample_count = SLICE_FULL
    elif case in [str(index) for index in REPRESENTATIVE_INDICES]:
        sound_index = int(case)
        sample_count = SLICE_REDUCED
    else:
        raise MutationError("unknown mutation-identity case: " + case)
    identity = SoundIdentity(sound_index)
    rows = inventory_rows()
    parameters = []
    for position, name in enumerate(rows):
        normalized = _normalized_value(position)
        parameters.append(
            {
                "name": name,
                "normalized": normalized,
                "physical": directed.physical_value(rows[name], normalized),
            }
        )
    noise = slot_bytes(NOISE_SEED, identity.noise_slot, sample_count)
    return {
        "schema": FIXTURE_SCHEMA,
        "schema_version": 1,
        "case": case,
        "sound_index": sound_index,
        "identity_batch_size": IDENTITY_BATCH_SIZE,
        "execution_status": "diagnostic-batch-1",
        "identity": identity.to_dict(IDENTITY_BATCH_SIZE),
        "parameters": parameters,
        "noise": {
            "seed": NOISE_SEED,
            "slot": identity.noise_slot,
            "sample_count": sample_count,
            "sha256": _sha256(noise),
            "samples_f32le_b64": _encode_bytes(noise),
        },
    }


def encode_fixture(document: Dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def decode_fixture(payload: bytes) -> Dict[str, Any]:
    document = json.loads(payload)
    _require(document.get("schema") == FIXTURE_SCHEMA,
             "payload is not a mutation-identity fixture")
    return document


def noise_bytes(document: Dict[str, Any]) -> bytes:
    return _decode_bytes(document["noise"]["samples_f32le_b64"])


def _strobe_lane(identity: Dict[str, Any]) -> List[float]:
    return [
        float(identity["sound_index"]),
        float(identity["render_batch_size"]),
        float(identity["render_batch_index"]),
        float(identity["render_slot"]),
        float(identity["upstream_batch_index"]),
        float(identity["upstream_slot"]),
        float(identity["noise_slot"]),
        1.0 if identity["is_train"] else 0.0,
    ]


def _parameter_lane(document: Dict[str, Any]) -> List[float]:
    rows = inventory_rows()
    weights = {
        name: ((position + 1) * 7919 % 997) / 997
        for position, name in enumerate(rows)
    }
    projection = math.fsum(
        weights[entry["name"]] * entry["physical"] for entry in document["parameters"]
    )
    return [math.sin(2.0 * math.pi * j / 32.0 + projection) for j in range(32)]


def _noise_lanes(document: Dict[str, Any]) -> List[float]:
    values = struct.unpack("<%df" % document["noise"]["sample_count"], noise_bytes(document))
    return list(values)


def lane_digests(document: Dict[str, Any]) -> Dict[str, str]:
    """Derived-lane digests recomputed from the presented document."""
    return {
        "identity.strobe": _lane_digest(_strobe_lane(document["identity"])),
        "parameter.control": _lane_digest(_parameter_lane(document)),
        "noise.source": _lane_digest(_noise_lanes(document)),
        "noise.vca": _lane_digest([0.3 * x for x in _noise_lanes(document)]),
    }


def blast_radius(document: Dict[str, Any], pristine: Dict[str, Any]) -> Tuple[str, ...]:
    before = lane_digests(pristine)
    after = lane_digests(document)
    return tuple(sorted(name for name in after if after[name] != before[name]))


def _applicator_batch_shift(instance: Dict[str, Any], document: Dict[str, Any]):
    wrong_batch = instance["magnitude"]["value"]
    if wrong_batch % 32:
        raise MutationError("wrong render batch size must be a multiple of 32")
    original = dict(document["identity"])
    document["identity"] = SoundIdentity(document["sound_index"]).to_dict(wrong_batch)
    wrong_field = next(
        (name for name in sorted(original) if original[name] != document["identity"][name]),
        None,
    )
    detail = {
        "wrong_render_batch_size": wrong_batch,
        "declared_render_batch_size": document["identity_batch_size"],
        "first_wrong_identity_field": wrong_field,
        "declared_truth": {name: original[name] for name in sorted(original)
                           if original[name] != document["identity"][name]},
    }
    return document, detail


def _applicator_train_flip(instance: Dict[str, Any], document: Dict[str, Any]):
    truth = SoundIdentity(document["sound_index"]).is_train
    presented = document["identity"]
    if presented["is_train"] != truth:
        return None, {"reason": "designation already diverges from the recomputed truth"}
    presented["is_train"] = not presented["is_train"]
    flipped = presented["is_train"]
    detail = {
        "field": "is_train",
        "declared_truth": truth,
        "presented": flipped,
        "train_test_block": document["sound_index"] // 1024,
    }
    return document, detail


def _parameter_entries(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    _require(
        [entry["name"] for entry in document["parameters"]] == canonical_names(),
        "fixture parameter order is not the canonical inventory order",
    )
    return document["parameters"]


def _applicator_shuffle(instance: Dict[str, Any], document: Dict[str, Any]):
    first = instance["configuration"]["first"]
    second = instance["configuration"]["second"]
    if first == second:
        return None, {"reason": "identical selection", "first": first, "second": second}
    entries = _parameter_entries(document)
    by_name = {entry["name"]: entry for entry in entries}
    by_name[first]["normalized"], by_name[second]["normalized"] = (
        by_name[second]["normalized"],
        by_name[first]["normalized"],
    )
    by_name[first]["physical"], by_name[second]["physical"] = (
        by_name[second]["physical"],
        by_name[first]["physical"],
    )
    positions = sorted((canonical_names().index(first), canonical_names().index(second)))
    detail = {
        "shuffled": [first, second],
        "first_wrong_canonical_parameter": canonical_names()[positions[0]],
        "first_wrong_position": positions[0],
    }
    return document, detail


def _wrong_physical(row: Dict[str, Any], normalized: float, mode: str) -> float:
    minimum, maximum = row["minimum"], row["maximum"]
    if mode == "linear-where-curved":
        return minimum + (maximum - minimum) * normalized
    if mode == "curved-where-linear":
        return minimum + (maximum - minimum) * normalized ** 2
    if mode == "swapped-bounds":
        return minimum + (maximum - minimum) * (1.0 - normalized)
    raise MutationError("unknown conversion mode: " + mode)


def _applicator_conversion(instance: Dict[str, Any], document: Dict[str, Any]):
    mode = instance["configuration"]["mode"]
    rows = inventory_rows()
    for position, entry in enumerate(_parameter_entries(document)):
        row = rows[entry["name"]]
        applies = (
            (mode == "linear-where-curved" and row["curve"] != 1)
            or (mode == "curved-where-linear" and row["curve"] == 1)
            or (mode == "swapped-bounds" and row["minimum"] != row["maximum"])
        )
        if not applies:
            continue
        declared = entry["physical"]
        entry["physical"] = _wrong_physical(row, entry["normalized"], mode)
        return document, {
            "mode": mode,
            "first_wrong_canonical_parameter": entry["name"],
            "first_wrong_position": position,
            "declared_physical": declared,
            "presented_physical": entry["physical"],
            "curve": row["curve"],
        }
    return None, {"reason": "no canonical parameter matches mode", "mode": mode}


def _replaced_noise(document: Dict[str, Any], seed: int, slot: int) -> Tuple[bytes, bytes]:
    original = noise_bytes(document)
    count = document["noise"]["sample_count"]
    replacement = slot_bytes(seed, slot, count)
    document["noise"] = {
        "seed": seed,
        "slot": slot,
        "sample_count": count,
        "sha256": _sha256(replacement),
        "samples_f32le_b64": _encode_bytes(replacement),
    }
    return original, replacement


def _applicator_slot_shift(instance: Dict[str, Any], document: Dict[str, Any]):
    shift = instance["magnitude"]["value"]
    wrong_slot = (document["noise"]["slot"] + shift) % NOISE_STREAMS
    seed = document["noise"]["seed"]
    original, replacement = _replaced_noise(document, seed, wrong_slot)
    return document, {
        "declared_truth_slot": SoundIdentity(document["sound_index"]).noise_slot,
        "wrong_slot": wrong_slot,
        "seed": seed,
        "original_noise_sha256": _sha256(original),
        "replacement_noise_sha256": _sha256(replacement),
    }


def _applicator_seed_shift(instance: Dict[str, Any], document: Dict[str, Any]):
    shift = instance["magnitude"]["value"]
    presented = document["noise"]
    wrong_seed = presented["seed"] + shift
    slot = presented["slot"]
    original, replacement = _replaced_noise(document, wrong_seed, slot)
    return document, {
        "declared_truth_seed": NOISE_SEED,
        "wrong_seed": wrong_seed,
        "slot": slot,
        "original_noise_sha256": _sha256(original),
        "replacement_noise_sha256": _sha256(replacement),
    }


FAMILY_APPLICATORS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]],
                                       Tuple[Optional[Dict[str, Any]], Dict[str, Any]]]] = {
    "identity.batch_shift": _applicator_batch_shift,
    "identity.train_test_flip": _applicator_train_flip,
    "param.positional_shuffle": _applicator_shuffle,
    "param.conversion_substitute": _applicator_conversion,
    "noise.slot_shift": _applicator_slot_shift,
    "noise.seed_shift": _applicator_seed_shift,
}


def _conversion_definition(configuration: Dict[str, Any], composes_with: List[str],
                           summary: str) -> Dict[str, Any]:
    return {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": configuration,
        "composes_with": composes_with,
        "summary": summary,
    }


FAMILY_OPERATORS: Dict[str, Dict[str, Any]] = {
    "identity.batch_shift": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "integer", "unit": "batch_size", "minimum": 32,
                      "maximum": 4096},
        "configuration": {},
        "composes_with": [],
        "summary": "present the global sound identity projected at the wrong "
        "render batch size (changed global/batch identity); the provenance "
        "recomputation must name the first wrong identity field before any "
        "perceptual row is computed",
    },
    "identity.train_test_flip": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "null", "unit": "none"},
        "configuration": {},
        "composes_with": [],
        "summary": "flip the presented train/test designation of the fixture "
        "identity; the is_train recomputation must fail at the provenance "
        "gate before any perceptual row is computed",
    },
    "param.positional_shuffle": _conversion_definition(
        {"first": canonical_names(), "second": canonical_names()},
        ["param.conversion_substitute"],
        "swap the declared value pairs of two canonical parameters "
        "(positional shuffle); the name-keyed interface accepts the mapping, "
        "so the pinned physical-conversion recomputation must name the first "
        "wrong canonical parameter",
    ),
    "param.conversion_substitute": _conversion_definition(
        {"mode": list(CONVERSION_MODES)},
        ["param.positional_shuffle"],
        "present one canonical parameter's physical value through the wrong "
        "normalized/physical conversion branch; the pinned conversion "
        "recomputation must name the first wrong canonical parameter",
    ),
    "noise.slot_shift": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "integer", "unit": "slot", "minimum": 1,
                      "maximum": NOISE_STREAMS - 1},
        "configuration": {},
        "composes_with": ["noise.seed_shift"],
        "summary": "present the canonical noise stream of a different slot "
        "(wrong noise slot); the exact noise identity record must fail even "
        "though first/second-order noise statistics stay plausible, and only "
        "the noise source/downstream lanes may diverge",
    },
    "noise.seed_shift": {
        "version": 1,
        "seam": FAMILY_SEAM,
        "sham": False,
        "magnitude": {"type": "integer", "unit": "seed_offset", "minimum": 1,
                      "maximum": 19},
        "configuration": {},
        "composes_with": ["noise.slot_shift"],
        "summary": "present a noise stream generated from a seed other than "
        "the declared seed 13 (wrong noise seed); the landed declared-seed "
        "refusal and the exact noise identity record must fail while the "
        "statistics stay plausible",
    },
}


def register_family() -> None:
    """Explicit trusted-code registration of the #31 family operators.

    Re-registration with an identical definition is a no-op; any drift
    between the registered and declared definition refuses.
    """
    for operator_id, definition in FAMILY_OPERATORS.items():
        registered = mutations.OPERATORS.get(operator_id)
        if registered is not None:
            _require(registered == definition,
                     "operator already registered with a different definition: "
                     + operator_id)
            continue
        mutations.register_operator(operator_id, definition)


class FamilySession(mutation_runtime.InjectionSession):
    """Landed injection session with the #31 family applicators installed."""

    def _apply(self, seam: str, instance: Dict[str, Any], value: Any) -> Any:
        applicator = FAMILY_APPLICATORS.get(instance["operator"])
        if applicator is None:
            return super()._apply(seam, instance, value)
        try:
            replacement, detail = applicator(instance, decode_fixture(value))
        except BaseException as error:
            self._record(
                instance,
                seam,
                "errored",
                {"error": type(error).__name__, "message": str(error)},
            )
            raise
        if replacement is None:
            self._record(instance, seam, "ineffective", dict(detail))
            return value
        payload = encode_fixture(replacement)
        if payload == value:
            self._record(
                instance,
                seam,
                "ineffective",
                dict(detail, reason="replacement equals original"),
            )
            return value
        self._record(instance, seam, "applied", detail)
        return payload


class IdentityFixtureHarness:
    """Deterministic fixture producer over the landed seam contract."""

    def __init__(self, root: Path, case: str):
        self.root = Path(root)
        self.case = case
        self.catalog = mutations.load_seam_catalog(self.root / mutations.SEAM_CATALOG_PATH)
        self.document = build_fixture(case)
        self.payload = encode_fixture(self.document)

    def binding(self) -> Dict[str, Any]:
        return {
            "case_id": "directed:mutation-identity:" + self.case,
            "partition": "development",
            "fixture_identity": mutation_runtime.sha256(self.payload),
        }

    def _produce(self) -> bytes:
        return self.payload

    def plain_attempt(self) -> mutation_runtime.AttemptResult:
        result = mutation_runtime.AttemptResult()
        result.store[PAYLOAD_PATH] = self.payload
        return result

    def attempt(self, plan: Dict[str, Any]) -> mutation_runtime.AttemptResult:
        result = mutation_runtime.AttemptResult()
        session = FamilySession(plan, {FAMILY_SEAM: self._produce})
        try:
            with session:
                result.store[PAYLOAD_PATH] = session.targets[FAMILY_SEAM]()
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
            [mutation_runtime.instance("mii-sham", "noop.sham", FAMILY_SEAM)],
            self.catalog,
        )

    def fault(self, instance_id: str, operator_id: str, magnitude: Any = None,
              configuration: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return mutations.make_plan(
            self.binding(),
            [mutation_runtime.instance(instance_id, operator_id, FAMILY_SEAM,
                                       magnitude, configuration)],
            self.catalog,
        )


def _metric(value: Any, unit: str) -> Dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "status": "valid",
        "reason": "measured",
        "coverage": "complete",
    }


def _measurement(kind: str, metrics: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema": "torchsynth-mutation-identity-measurement",
        "schema_version": 1,
        "estimator": {"name": kind, "version": "1"},
        "settings": {"definition": "mutations_identity-v1",
                     "preparation": "identity-native"},
        "implementation": {"python": sys.version.split()[0]},
        "metrics": metrics,
    }


def _family_rows(kind: str, metrics: Dict[str, Dict[str, Any]], limits: Dict[str, Limit],
                 case_id: str, trace: str) -> List[Dict[str, Any]]:
    rubric = Rubric("mutation-identity-detectors", "1", limits)
    rows, _ = scorecard_rows(
        _measurement(kind, metrics),
        case_id=case_id,
        partition="development",
        trace=trace,
        rubric=rubric,
    )
    return rows


def _provenance_gate(document: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute the landed identity contract against the presented projection."""
    presented = document["identity"]
    truth = SoundIdentity(document["sound_index"]).to_dict(document["identity_batch_size"])
    wrong_field = next(
        (name for name in sorted(presented)
         if name not in truth or presented[name] != truth[name]),
        None,
    )
    train_ok = "is_train" not in presented or presented["is_train"] == truth["is_train"]
    return {
        "provenance_match": wrong_field is None,
        "first_wrong_identity_field": wrong_field,
        "train_test_match": train_ok,
        "declared_truth": truth,
    }


def _parameter_gate(document: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute the pinned conversion; report the first wrong canonical parameter."""
    rows = inventory_rows()
    for position, entry in enumerate(_parameter_entries(document)):
        expected = directed.physical_value(rows[entry["name"]], entry["normalized"])
        if entry["physical"] != expected:
            return {
                "first_wrong_position": position,
                "first_wrong_canonical_parameter": entry["name"],
                "declared_physical": expected,
                "presented_physical": entry["physical"],
            }
    return {"first_wrong_position": -1, "first_wrong_canonical_parameter": None}


def _noise_gates(document: Dict[str, Any], pristine: Dict[str, Any], case_id: str):
    """Exact noise identity plus the preserved statistical plausibility rows."""
    truth_slot = SoundIdentity(pristine["sound_index"]).noise_slot
    truth = slot_bytes(NOISE_SEED, truth_slot, pristine["noise"]["sample_count"])
    candidate = noise_bytes(document)
    reference_record = spectral_estimators.NoiseRecord(
        truth, (len(truth) // 4,), "<f4", NOISE_SEED, truth_slot
    )
    candidate_record = spectral_estimators.NoiseRecord(
        candidate,
        (len(candidate) // 4,),
        "<f4",
        document["noise"]["seed"],
        document["noise"]["slot"],
    )
    identity_rows, _ = spectral_estimators.scorecard_rows(
        spectral_estimators.noise_identity(reference_record, candidate_record),
        case_id=case_id,
        trace="synthetic:noise-exact",
        limits={"noise.identity": Limit(1, 0.0, "1", EXACT_SOURCE)},
    )
    values = list(struct.unpack("<%df" % (len(candidate) // 4), candidate))
    statistics = spectral_estimators.noise_statistics(values, sample_rate_hz=AUDIO_RATE_HZ)
    plausibility = {
        "noise.mean": Limit(0.0, PLAUSIBILITY_BAND, "amplitude", PLAUSIBILITY_SOURCE),
        "noise.variance": Limit(1.0 / 3.0, PLAUSIBILITY_BAND, "(amplitude)^2",
                                PLAUSIBILITY_SOURCE),
        **{
            "noise.ac.%d" % lag: Limit(0.0, PLAUSIBILITY_BAND, "1", PLAUSIBILITY_SOURCE)
            for lag in NOISE_LAGS
        },
    }
    statistics_rows, _ = spectral_estimators.scorecard_rows(
        statistics,
        case_id=case_id,
        trace="synthetic:noise-statistics",
        limits=plausibility,
    )
    return identity_rows, statistics_rows


def detector_pipeline(document: Dict[str, Any], pristine: Dict[str, Any], case_id: str,
                      expected_blast: Tuple[str, ...] = ()
                      ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Ordered detector rows: provenance, parameter, then perceptual noise rows.

    A provenance or parameter refusal happens before any perceptual row is
    computed; the gate record states whether the perceptual rows were
    reached and why not.
    """
    rows: List[Dict[str, Any]] = []
    provenance = _provenance_gate(document)
    rows.extend(_family_rows(
        "mutation-identity-provenance",
        {
            "identity.provenance_match": _metric(
                int(provenance["provenance_match"]), "1"
            ),
            "identity.train_test_match": _metric(
                int(provenance["train_test_match"]), "1"
            ),
        },
        {
            "identity.provenance_match": Limit(1, 0.0, "1", PROVENANCE_SOURCE),
            "identity.train_test_match": Limit(1, 0.0, "1", PROVENANCE_SOURCE),
        },
        case_id,
        "synthetic:identity-provenance",
    ))
    parameter = _parameter_gate(document)
    rows.extend(_family_rows(
        "mutation-identity-parameter",
        {"parameter.first_wrong_position": _metric(
            parameter["first_wrong_position"], "parameter_position"
        )},
        {"parameter.first_wrong_position": Limit(-1, 0.0, "parameter_position",
                                                 CONVERSION_SOURCE)},
        case_id,
        "synthetic:parameter-conversion",
    ))
    upstream_ok = (
        provenance["provenance_match"]
        and provenance["train_test_match"]
        and parameter["first_wrong_position"] == -1
    )
    if not upstream_ok:
        reason = (
            "provenance refusal at " + str(provenance["first_wrong_identity_field"])
            if not provenance["provenance_match"]
            else "train/test designation refusal"
            if not provenance["train_test_match"]
            else "parameter conversion refusal at "
            + str(parameter["first_wrong_canonical_parameter"])
        )
        return rows, {
            "perceptual_rows_reached": False,
            "gate_reason": reason,
            "provenance": provenance,
            "parameter": parameter,
        }
    identity_rows, statistics_rows = _noise_gates(document, pristine, case_id)
    rows.extend(identity_rows)
    rows.extend(statistics_rows)
    diverged = blast_radius(document, pristine)
    rows.extend(_family_rows(
        "mutation-identity-blast",
        {"blast.exact_match": _metric(
            int(set(diverged) == set(expected_blast)), "1"
        )},
        {"blast.exact_match": Limit(1, 0.0, "1", BLAST_SOURCE)},
        case_id,
        "synthetic:blast-radius",
    ))
    return rows, {
        "perceptual_rows_reached": True,
        "gate_reason": "provenance and parameter gates passed",
        "provenance": provenance,
        "parameter": parameter,
        "diverged_lanes": list(diverged),
    }


def _verdict_of(rows: List[Dict[str, Any]], property_name: str) -> str:
    for row in rows:
        if row["property"] == property_name:
            return row["verdict"]
    raise MutationError("rows carry no property: " + property_name)


def _row(fault: str, operator: str, seam: str, downstream: str, expected: str,
         control_ok: bool, observed: Optional[str]) -> Dict[str, Any]:
    return {
        "fault": fault,
        "operator": operator,
        "seam": seam,
        "downstream": downstream,
        "expected_refusal": expected,
        "observed_refusal": observed,
        "tripped": bool(control_ok and observed is not None),
        "control_accepted": bool(control_ok),
    }


def _receipts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wanted = (
        "identity.provenance_match",
        "identity.train_test_match",
        "parameter.first_wrong_position",
        "noise.identity",
        "blast.exact_match",
        *( "noise.ac.%d" % lag for lag in NOISE_LAGS ),
    )
    return [
        {
            "property": row["property"],
            "verdict": row["verdict"],
            "observed": row["observed"],
            "unit": row["unit"],
        }
        for row in rows
        if row["property"] in wanted
    ]


def _resolved_request_witness(document: Dict[str, Any]) -> Dict[str, Any]:
    """The landed interface verdict on the presented fixture (directed cases)."""
    try:
        ResolvedRequest(
            document["sound_index"],
            {entry["name"]: entry["normalized"] for entry in document["parameters"]},
            {
                "seed": document["noise"]["seed"],
                "slot": document["noise"]["slot"],
                "sample_count": document["noise"]["sample_count"],
                "sha256": document["noise"]["sha256"],
                "samples": noise_bytes(document),
            },
            physical={entry["name"]: entry["physical"] for entry in document["parameters"]},
            execution_status=document["execution_status"],
        )
    except ValueError as error:
        return {"accepted": False, "refusal": str(error)}
    return {"accepted": True, "refusal": None}


def fault_matrix(root: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Run every family fault against its landed detector, with clean controls."""
    root = Path(root)
    register_family()
    entries: List[Dict[str, Any]] = []
    case_records: List[Dict[str, Any]] = []

    fault_names = canonical_names()
    fault_specs = (
        ("identity-batch-shift-128", "identity.batch_shift", 128, None,
         "identity.SoundIdentity.to_dict recomputation at the declared render "
         "batch size",
         "provenance mismatch at render_batch_index before perceptual rows"),
        ("train-test-designation-flip", "identity.train_test_flip", None, None,
         "identity.SoundIdentity.is_train recomputation",
         "is_train designation mismatch at the provenance gate before "
         "perceptual rows"),
        ("positional-parameter-shuffle", "param.positional_shuffle", None,
         {"first": fault_names[0], "second": fault_names[1]},
         "directed.physical_value pinned-conversion recomputation (the "
         "name-keyed interface accepts the shuffle; the conversion "
         "recomputation is the refusal)",
         "first wrong canonical parameter named at the provenance-ordered "
         "parameter gate"),
        ("wrong-normalized-physical-conversion", "param.conversion_substitute",
         None, {"mode": "linear-where-curved"},
         "directed.physical_value pinned-conversion recomputation",
         "first wrong canonical parameter named at the provenance-ordered "
         "parameter gate"),
        ("wrong-noise-slot", "noise.slot_shift", 5, None,
         "spectral_estimators.noise_identity exact record comparison + "
         "float_interfaces.ResolvedRequest diagnostic-batch-1 slot-copy "
         "refusal",
         "noise.identity FAIL for the wrong selected stream while the noise "
         "statistics stay inside the declared plausibility band"),
        ("wrong-noise-seed", "noise.seed_shift", 1, None,
         "float_interfaces.ResolvedRequest declared-seed refusal + "
         "spectral_estimators.noise_identity exact record comparison",
         "noise seed must be the declared seed 13; noise.identity FAIL while "
         "the statistics stay plausible"),
    )

    for case_name in DIRECTED_CASES:
        harness = IdentityFixtureHarness(root, case_name)
        case_id = "directed:mutation-identity:" + case_name
        control_rows, control_gate = detector_pipeline(
            harness.document, harness.document, case_id + "-control"
        )
        control_witness = _resolved_request_witness(harness.document)
        control_ok = (
            control_gate["perceptual_rows_reached"]
            and _verdict_of(control_rows, "identity.provenance_match") == "PASS"
            and _verdict_of(control_rows, "identity.train_test_match") == "PASS"
            and _verdict_of(control_rows, "parameter.first_wrong_position") == "PASS"
            and _verdict_of(control_rows, "noise.identity") == "PASS"
            and control_witness["accepted"]
            and all(
                _verdict_of(control_rows, "noise.ac.%d" % lag) == "PASS"
                for lag in NOISE_LAGS
            )
        )
        for fault_name, operator_id, magnitude, configuration, downstream, expected in fault_specs:
            plan = harness.fault("mii-" + fault_name, operator_id, magnitude,
                                 configuration)
            attempt = harness.attempt(plan)
            document = decode_fixture(attempt.store[PAYLOAD_PATH])
            rows, gate = detector_pipeline(document, harness.document, case_id,
                                           EXPECTED_BLAST[operator_id])
            observed_parts: List[str] = []
            if gate["perceptual_rows_reached"]:
                if _verdict_of(rows, "noise.identity") == "FAIL":
                    observed_parts.append(
                        "noise.identity FAIL while noise statistics stay inside "
                        "the declared plausibility band"
                    )
                diverged = gate["diverged_lanes"]
                expected_blast = set(EXPECTED_BLAST[operator_id])
                if set(diverged) == expected_blast:
                    observed_parts.append(
                        "blast radius confined to " + ", ".join(sorted(diverged))
                    )
                if operator_id in ("noise.slot_shift", "noise.seed_shift"):
                    witness = _resolved_request_witness(document)
                    if not witness["accepted"]:
                        observed_parts.append("ResolvedRequest: " + witness["refusal"])
            else:
                observed_parts.append(
                    gate["gate_reason"] + "; perceptual rows not reached"
                )
            if operator_id in ("param.positional_shuffle", "param.conversion_substitute"):
                observed_parts.append(
                    "first wrong canonical parameter: "
                    + str(gate["parameter"]["first_wrong_canonical_parameter"])
                    + " at position "
                    + str(gate["parameter"]["first_wrong_position"])
                )
            observed = "; ".join(observed_parts) if observed_parts else None
            entries.append(_row(fault_name, operator_id, FAMILY_SEAM, downstream,
                                expected, control_ok, observed))
            case_records.append(
                {
                    "case": case_name,
                    "plan": plan,
                    "attempt": attempt,
                    "rows": _receipts(rows),
                    "gate": {
                        "perceptual_rows_reached": gate["perceptual_rows_reached"],
                        "gate_reason": gate["gate_reason"],
                    },
                    "witness": _resolved_request_witness(document),
                    "faults": [fault_name],
                }
            )

        composed_plan = mutations.make_plan(
            harness.binding(),
            [
                mutation_runtime.instance("mii-composed-slot", "noise.slot_shift",
                                          FAMILY_SEAM, 5),
                mutation_runtime.instance("mii-composed-seed", "noise.seed_shift",
                                          FAMILY_SEAM, 1),
            ],
            harness.catalog,
        )
        composed_attempt = harness.attempt(composed_plan)
        composed_document = decode_fixture(composed_attempt.store[PAYLOAD_PATH])
        composed_rows, composed_gate = detector_pipeline(
            composed_document, harness.document, case_id, NOISE_TRUTH_LANES
        )
        composed_observed = None
        if (
            [event["status"] for event in composed_attempt.events]
            == ["applied", "applied"]
            and _verdict_of(composed_rows, "noise.identity") == "FAIL"
            and set(composed_gate["diverged_lanes"]) == set(NOISE_TRUTH_LANES)
        ):
            composed_observed = (
                "declared-order applied events; composed wrong-slot and "
                "wrong-seed stream fails the exact identity record with the "
                "blast radius confined to the noise lanes"
            )
        entries.append(_row(
            "composed-wrong-slot-then-wrong-seed",
            "noise.slot_shift + noise.seed_shift",
            FAMILY_SEAM,
            "in-band declared-order events + spectral_estimators.noise_identity",
            "declared-order applied events; composed stream fails "
            "noise.identity while statistics stay plausible",
            control_ok,
            composed_observed,
        ))
        case_records.append(
            {
                "case": case_name,
                "plan": composed_plan,
                "attempt": composed_attempt,
                "rows": _receipts(composed_rows),
                "gate": {
                    "perceptual_rows_reached": composed_gate["perceptual_rows_reached"],
                    "gate_reason": composed_gate["gate_reason"],
                },
                "witness": _resolved_request_witness(composed_document),
                "faults": ["composed-wrong-slot-then-wrong-seed"],
            }
        )
    return entries, case_records


def representative_random_receipts(root: Path) -> List[Dict[str, Any]]:
    """Each family fault on the fixed representative non-directed cases.

    The cases are constants, not samples: no randomness is consumed, and the
    set spans noise slots, upstream batches and both train/test designations.
    """
    root = Path(root)
    register_family()
    receipts: List[Dict[str, Any]] = []
    for sound_index in REPRESENTATIVE_INDICES:
        harness = IdentityFixtureHarness(root, str(sound_index))
        identity = SoundIdentity(sound_index)
        case_id = "representative:mutation-identity:%d" % sound_index
        control_rows, _ = detector_pipeline(
            harness.document, harness.document, case_id + "-control"
        )
        control_ok = all(
            _verdict_of(control_rows, name) == "PASS"
            for name in ("identity.provenance_match", "identity.train_test_match",
                         "parameter.first_wrong_position", "noise.identity")
        )
        for operator_id, magnitude, configuration in (
            ("identity.batch_shift", 128, None),
            ("identity.train_test_flip", None, None),
            ("param.positional_shuffle", None,
             {"first": canonical_names()[0], "second": canonical_names()[1]}),
            ("param.conversion_substitute", None,
             {"mode": "linear-where-curved"}),
            ("noise.slot_shift", 5, None),
            ("noise.seed_shift", 1, None),
        ):
            attempt = harness.attempt(
                harness.fault("mii-r%d" % sound_index, operator_id, magnitude,
                              configuration)
            )
            document = decode_fixture(attempt.store[PAYLOAD_PATH])
            rows, gate = detector_pipeline(document, harness.document, case_id,
                                           EXPECTED_BLAST[operator_id])
            tripped = False
            named = None
            if gate["perceptual_rows_reached"]:
                tripped = _verdict_of(rows, "noise.identity") == "FAIL" and set(
                    gate["diverged_lanes"]
                ) == set(EXPECTED_BLAST[operator_id])
            else:
                tripped = (
                    _verdict_of(rows, "identity.provenance_match") == "FAIL"
                    or _verdict_of(rows, "identity.train_test_match") == "FAIL"
                    or _verdict_of(rows, "parameter.first_wrong_position") == "FAIL"
                )
                named = gate["gate_reason"]
            if operator_id in ("param.positional_shuffle",
                               "param.conversion_substitute"):
                named = (
                    "first wrong canonical parameter: "
                    + str(gate["parameter"]["first_wrong_canonical_parameter"])
                )
            receipts.append(
                {
                    "case": str(sound_index),
                    "sound_index": sound_index,
                    "noise_slot": identity.noise_slot,
                    "is_train": identity.is_train,
                    "operator": operator_id,
                    "control_accepted": control_ok,
                    "tripped": bool(control_ok and tripped),
                    "named": named,
                    "perceptual_rows_reached": gate["perceptual_rows_reached"],
                }
            )
    return receipts


def qualification_controls(root: Path) -> Dict[str, Any]:
    """Ordinary/empty/sham controls and cleanup guarantees for the family."""
    import random

    root = Path(root)
    register_family()
    harness = IdentityFixtureHarness(root, "directed-train")
    plain = harness.plain_attempt()
    empty = harness.attempt(harness.empty_plan())
    sham = harness.attempt(harness.sham_plan())
    rng_before = random.getstate()
    harness.attempt(harness.sham_plan())
    plain_again = harness.plain_attempt()
    rng_untouched = random.getstate() == rng_before

    targets = {FAMILY_SEAM: harness._produce}
    crash_plan = mutations.make_plan(
        harness.binding(),
        [mutation_runtime.instance("mii-crash", "crash.producer", FAMILY_SEAM)],
        harness.catalog,
    )
    crashed = False
    session = FamilySession(crash_plan, targets)
    try:
        with session:
            targets[FAMILY_SEAM]()
    except mutation_runtime.ProducerCrash:
        crashed = True
    clean_after_crash = targets[FAMILY_SEAM] == harness._produce

    faulted = IdentityFixtureHarness(root, "directed-train")
    sibling = IdentityFixtureHarness(root, "directed-test")
    sibling_before = sibling.payload
    faulted.attempt(faulted.fault("mii-sibling", "noise.slot_shift", 5))
    sibling_unchanged = IdentityFixtureHarness(root, "directed-test").payload == sibling_before

    witness = _resolved_request_witness(faulted.document)
    shuffled = decode_fixture(
        faulted.attempt(
            faulted.fault(
                "mii-witness-shuffle",
                "param.positional_shuffle",
                configuration={
                    "first": canonical_names()[0],
                    "second": canonical_names()[1],
                },
            )
        ).store[PAYLOAD_PATH]
    )
    shuffled_witness = _resolved_request_witness(shuffled)
    shifted = faulted.fault("mii-shift-magnitude", "noise.slot_shift", 3)
    other_shift = faulted.fault("mii-shift-magnitude", "noise.slot_shift", 7)
    return {
        "plain_vs_empty_plan_bytes_identical": plain.store == empty.store,
        "plain_vs_sham_bytes_identical": plain.store == sham.store,
        "plain_rerun_deterministic": plain.store == plain_again.store,
        "empty_plan_events_complete": empty.events_summary.get("complete") is True
        and empty.events_summary.get("observed") == 0,
        "sham_events_marked_and_ineffective": bool(sham.events)
        and all(event["sham"] for event in sham.events)
        and all(event["status"] == "ineffective" for event in sham.events),
        "sham_plan_identity_distinct": harness.sham_plan()["plan_id"]
        != harness.empty_plan()["plan_id"],
        "plan_identity_tracks_magnitude": shifted["plan_id"] != other_shift["plan_id"],
        "global_rng_untouched_by_attempts": rng_untouched,
        "cleanup_after_producer_crash": crashed and clean_after_crash,
        "no_errored_events_in_controls": plain.errored is None
        and empty.errored is None
        and sham.errored is None,
        "sibling_case_bytes_unchanged_by_faulted_case": sibling_unchanged,
        "resolved_request_accepts_clean_directed_case": witness["accepted"],
        "parameter_interface_accepts_name_consistent_shuffle": shuffled_witness[
            "accepted"
        ],
    }
