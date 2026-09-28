#!/usr/bin/env python3
"""Cross-artifact consistency checks for the canonical CPU reference (issue #3).

Issue #3 is the Epic #1 phase tracker whose four work units (#10 reproducible
release-era environment, #11 selected-commit vs v1.0.2 equivalence, #12 batched
repeatability and the runtime decision record, #88 single-sound scalar
execution) have all landed. Its closure condition is not "the leaves landed" —
it is "the leaves landed *and their evidence is consistent*". Consistency
checked once by reading is a claim with a shelf life; consistency checked by a
CI-run program is a property.

So this module re-derives, from the committed artifacts alone, the specific
cross-record facts the four leaves assert about each other, and reports every
disagreement. It is deliberately standard-library only and never renders audio:
``.github/workflows/ci.yml`` runs it (through
``tests/test_reference_consolidation.py``) on a runner with no torch, no
torchsynth and no docker, so it must be able to say something true there. What
it therefore does *not* do is re-measure the qualification matrix — that needs
the pinned container and a sanctioned host, and it is the job of
``env/release-era/qualify_repeatability.py`` and the two sentinel workflows.
The division of labour is the point: the sentinels prove the *bytes* still
reproduce, and this module proves the *records about those bytes* still agree
with each other and with the tree.

The checks that motivated writing it are :func:`check_publication_pins` and
:func:`check_dispatch_profile_sites`. DR-0009 amendment A2 republished
``sim/reference/repeatability-runtime.json`` and left one of the two hard-coded
digest gates of that file behind; the amendment's own note drew the lesson ("a
republish of a digest-pinned reference file must sweep the repository for every
companion pin of that file before merge") but nothing enforced it, so the stale
gate sat on main refusing every real render on the only path that uses it. The
same amendment added two ISA pins to ``profile_environment["release"]``, which
the two sentinel spawn paths picked up for free, while six other host spawn paths
kept restating the pre-amendment pins as literals — each one a render that the
worker refuses on sight. Both classes of defect are invisible to the sentinels by
construction: the bytes were fine, and the sentinels' own spawn already derived
from the plan; what was stale was everything *referring* to them.

Run directly for a report::

    python3 tools/check_reference_consolidation.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.digest import sha256_bytes as digest  # noqa: E402

#: This module's own repo-relative path. The completeness scan in
#: :func:`check_publication_pins` searches every ``*.py`` for
#: :data:`PUBLICATION_PIN_MESSAGE`, and this module necessarily *contains* that
#: string — as the constant being searched for. Naming the sentinel is not gating
#: a render on it, so the scanner excludes its own source. It cannot instead be
#: registered in :data:`PUBLICATION_PIN_SITES`: that arm additionally requires
#: each site to pin the publication's committed digest as a literal, which a
#: checker that *recomputes* the digest deliberately does not, so registering it
#: would trade one false failure for another and assert something untrue.
SELF_RELATIVE = Path(__file__).resolve().relative_to(ROOT).as_posix()

#: Repo-relative directory prefixes the completeness scans never descend into:
#: git's own object store and Loom's orchestration tree (which contains a full
#: nested checkout per active worktree).
UNSCANNED_PREFIXES = (".git/", ".loom/")

PUBLICATION = "sim/reference/repeatability-runtime.json"
MATRIX = "env/release-era/repeatability-matrix.json"
SOURCE_COMPARISON = "env/release-era/source-comparison.json"
SOURCE_EQUIVALENCE = "sim/reference/source-equivalence.json"
UPSTREAM = "spec/reference/upstream.json"

#: Every site that hard-codes the digest of :data:`PUBLICATION` as an admission
#: gate. The literals stay duplicated in the gate sites on purpose — a gate that
#: reads its own expected value out of the repository it is gating proves
#: nothing — so this registry plus :func:`check_publication_pins` is what keeps
#: the independent copies in agreement. ``message`` is the refusal string each
#: site raises, and is also used to prove the registry is complete.
PUBLICATION_PIN_SITES = (
    "src/torchsynth_voice/artifact_renderer.py",
    "env/release-era/render_artifact.py",
)
PUBLICATION_PIN_MESSAGE = "ratified runtime publication changed"

#: ``(host spawn path, worker it launches)`` for every dispatch of a worker that
#: asserts the plan's ``profile_environment["release"]`` before rendering. The
#: declaration has exactly one home — ``repeatability-matrix.json`` — so a spawn
#: must *derive* its ``docker run`` environment from the plan and never restate a
#: pin as a literal. DR-0009 amendment A2 added two ISA pins to the plan; the two
#: sentinel spawn paths already derived it, and the other six restated the
#: pre-amendment pins as literals and therefore refused at the worker. Five are
#: repaired; the sixth is in :data:`DISPATCH_RECAPTURE_GATED`.
DISPATCH_SPAWN_SITES = (
    ("env/release-era/qualify_repeatability.py", "env/release-era/qualify_repeatability.py"),
    ("src/torchsynth_voice/artifact_renderer.py", "env/release-era/render_artifact.py"),
    ("tools/qualify_trace_artifacts.py", "env/release-era/render_artifact.py"),
    ("tools/capture_float_sources.py", "env/release-era/render_artifact.py"),
    ("tools/probe_trace_registry.py", "tools/probe_trace_registry.py"),
    ("tools/qualify_mutations_runtime.py", "env/release-era/mutation_worker.py"),
    ("tools/qualify_trace_capture.py", "env/release-era/capture_traces.py"),
)
#: Derivation forms accepted as "reads the plan instead of restating it": the
#: shared helper, or ``qualify_repeatability.py``'s equivalent inline
#: comprehension over the profile it loaded.
DISPATCH_DERIVATIONS = ("dispatch_flags(", "profile.items()")

#: Spawn sites that still restate the pins, and why they may. A committed record
#: pins the spawn tool's *own* digest as producer provenance and a test enforces
#: it, so repairing the spawn requires a recapture on the DR-0006 measurement
#: host — not something CI or a documentation pass can perform or fake. These
#: are exempt from the derivation rule and the literal scan, and in exchange
#: must keep the producer binding intact and stay named in the consolidation
#: record's open divergences. ``{spawn: record pinning it}``.
DISPATCH_RECAPTURE_GATED = {
    "tools/probe_trace_registry.py": "sim/reference/trace-registry-prototype.json",
}
CONSOLIDATION_RECORD = "spec/CANONICAL-REFERENCE-QUALIFICATION.md"

#: Preregistered probe identities (``env/release-era/repeatability-matrix.json``)
#: and the reason each one is in the matrix, per issue #3's required work.
EXPECTED_CASES = {
    "global-0": 0,
    "global-31": 31,
    "global-32": 32,
    "global-9215": 9215,
    "global-9216": 9216,
    "global-39942": 39942,
}

#: Modules that admit a source tree before importing torch. The pairs are
#: (path, name of the call that validates the source manifest).
SOURCE_GATED_WORKERS = (
    ("env/release-era/probe.py", "validate_source"),
    ("env/release-era/qualify_repeatability.py", "validate_source"),
    ("env/release-era/qualify_scalar.py", "validate_source"),
    ("env/release-era/render_artifact.py", "source_gate"),
)

HEX64 = re.compile(r"\b[0-9a-f]{64}\b")


def read_bytes(relative: str) -> bytes:
    return (ROOT / relative).read_bytes()


def read_json(relative: str):
    return json.loads(read_bytes(relative))


def canonical_json_bytes(value) -> bytes:
    """``env/release-era/probe.py``'s ``json_bytes``, reimplemented.

    Duplicated rather than imported because that module targets the container's
    Python 3.9 and imports by bare module name from ``env/release-era``; the
    three arguments below *are* the whole definition, and
    :func:`check_plan_binding` proves the reimplementation still reproduces the
    committed ``plan_sha256``.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def scanned_python_sources(root: Path) -> list[tuple[Path, str]]:
    """``(path, repo-relative path)`` for every ``*.py`` the scans must visit.

    The two completeness scans below are what make the registries above
    non-authoritative: an *unregistered* seventh spawn site or gate site is still
    refused, because the scan reads the whole tree rather than the registry. That
    guarantee is only as good as the scan's reachability, so the exclusion list is
    matched on the path **relative to** ``root``, never on the absolute path.

    Matching absolutely — ``".loom" in path.parts`` — is a silent no-op, not a
    narrow miss: a Loom worktree lives at ``<repo>/.loom/worktrees/issue-N``, so
    ``.loom`` is an ancestor component of *every* file in that checkout and the
    exclusion swallows the entire tree. Both scans then pass vacuously in exactly
    the environment every Builder and Doctor runs them from, while CI's plain
    checkout still enforces them — a check that could not run looking like one
    that passed, which ``.loom/docs/ci-principles.md`` names as the failure mode
    to design against. ``ScanReachabilityTests`` in
    ``tests/test_reference_consolidation.py`` pins this from both checkout shapes.
    """
    sources: list[tuple[Path, str]] = []
    for path in sorted(root.glob("**/*.py")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(UNSCANNED_PREFIXES):
            continue
        sources.append((path, relative))
    return sources


def check_publication_pins() -> list[str]:
    """Every hard-coded gate on the ratified publication names the live file.

    DR-0009 amendment A2, "companion republish pin".
    """
    errors: list[str] = []
    actual = digest(read_bytes(PUBLICATION))
    for relative in PUBLICATION_PIN_SITES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        if PUBLICATION_PIN_MESSAGE not in text:
            errors.append(
                f"{relative} is registered as a {PUBLICATION} gate site but no longer "
                f"raises {PUBLICATION_PIN_MESSAGE!r}"
            )
            continue
        if actual not in HEX64.findall(text):
            errors.append(
                f"{relative} gates {PUBLICATION} but does not pin its committed digest "
                f"{actual}: a republish moved the file and left this gate behind, so "
                f"every render through it refuses with {PUBLICATION_PIN_MESSAGE!r}"
            )
    registered = set(PUBLICATION_PIN_SITES)
    for path, relative in scanned_python_sources(ROOT):
        # SELF_RELATIVE names the sentinel as the constant this scan searches
        # for; it gates nothing on it. See SELF_RELATIVE for why registering it
        # is not the alternative.
        if relative == SELF_RELATIVE or relative in registered:
            continue
        if PUBLICATION_PIN_MESSAGE in path.read_text(encoding="utf-8"):
            errors.append(
                f"{relative} gates the ratified publication but is not in "
                f"PUBLICATION_PIN_SITES, so no republish sweep covers it"
            )
    return errors


def check_dispatch_profile_sites() -> list[str]:
    """Every release-profile spawn describes the environment the plan declares.

    The worker side of each pair asserts the plan's whole
    ``profile_environment["release"]`` and refuses anything else, so a spawn that
    restates the pins as literals is not merely untidy — it is a render that
    cannot succeed. This is the second half of the DR-0009 A2 defect class:
    :func:`check_publication_pins` covers a stale digest pin, this covers a
    stale environment pin, and both are invisible to the render sentinels
    because the sentinels' own spawn already derives from the plan.

    One registered site cannot be repaired without a recapture on the DR-0006
    host (:data:`DISPATCH_RECAPTURE_GATED`). It is held to a different, equally
    checkable standard: its producer binding must still be intact and the gap
    must still be named in the consolidation record. "Known and recorded" is a
    state this function enforces; "quietly excluded" is not available.
    """
    errors: list[str] = []
    profile = read_json(MATRIX)["profile_environment"]["release"]
    literals = {
        key: key + "=" + value for key, value in profile.items() if value is not None
    }
    # A missing consolidation record is not fatal here: it simply means no gap
    # is recorded, which the recapture-gated branch reports as such.
    record_path = ROOT / CONSOLIDATION_RECORD
    record_text = record_path.read_text(encoding="utf-8") if record_path.is_file() else ""
    for spawn, worker in DISPATCH_SPAWN_SITES:
        for relative in (spawn, worker):
            if not (ROOT / relative).is_file():
                errors.append(f"{relative} is registered as a dispatch site but is missing")
        if not (ROOT / spawn).is_file() or not (ROOT / worker).is_file():
            continue
        spawn_text = (ROOT / spawn).read_text(encoding="utf-8")
        worker_text = (ROOT / worker).read_text(encoding="utf-8")
        if Path(worker).stem not in spawn_text:
            errors.append(
                f"{spawn} is registered as the spawn of {worker} but never names it; "
                "the pairing is stale and this registry entry proves nothing"
            )
        if spawn in DISPATCH_RECAPTURE_GATED:
            errors.extend(recapture_gated_spawn_errors(spawn, record_text))
        elif not any(form in spawn_text for form in DISPATCH_DERIVATIONS):
            errors.append(
                f"{spawn} spawns {worker} but does not derive its dispatch environment "
                f"from {MATRIX}; restating the pins lets the plan move without it"
            )
        if "profile_environment" not in worker_text:
            errors.append(
                f"{worker} no longer asserts the plan's profile_environment, so nothing "
                f"would catch {spawn} dispatching the wrong environment"
            )
    # Completeness: no non-test module may restate a declared pin as a literal,
    # registered or not. Test modules may, and do — asserting that a pin reaches
    # the container is exactly their job — and so may the recapture-gated sites,
    # which pay for the exemption above.
    for path, relative in scanned_python_sources(ROOT):
        if path.name.startswith("test_") or relative in DISPATCH_RECAPTURE_GATED:
            continue
        text = path.read_text(encoding="utf-8")
        for key, literal in sorted(literals.items()):
            if literal in text:
                errors.append(
                    f"{relative} hard-codes the dispatch pin {literal!r}; {key} is "
                    f"declared in {MATRIX} and must be read from there so a plan "
                    "amendment reaches every spawn"
                )
    return errors


def recapture_gated_spawn_errors(spawn: str, record_text: str) -> list[str]:
    """A spawn whose repair needs a recapture stays pinned *and* stays recorded.

    Editing such a spawn breaks the ``producer_sha256`` binding of a committed
    experiment — a test enforces it — so the correct disposition is to leave the
    code alone and keep the gap visible, never to re-pin the committed record to
    a tool that did not produce it.
    """
    errors: list[str] = []
    record = DISPATCH_RECAPTURE_GATED[spawn]
    producers = read_json(record).get("producer_sha256", {})
    if spawn not in producers:
        errors.append(
            f"{spawn} is exempted from the derivation rule because {record} pins its "
            "producer digest, but that record no longer pins it: either repair the "
            "spawn or remove the exemption"
        )
    elif digest(read_bytes(spawn)) != producers[spawn]:
        errors.append(
            f"{spawn} no longer matches the producer digest {record} recorded; the "
            "committed experiment no longer describes the tool that produced it"
        )
    if spawn not in record_text:
        errors.append(
            f"{spawn} still restates a declared dispatch pin but is not named in "
            f"{CONSOLIDATION_RECORD}; a known gap that is not recorded is an "
            "undisclosed one"
        )
    return errors


def check_environment_definition() -> list[str]:
    """AC1: the canonical environment is reproducible from committed files."""
    errors: list[str] = []
    matrix = read_json(MATRIX)
    release = matrix["runtime_definitions"]["release"]
    for relative, key in (
        ("env/release-era/requirements.lock", "lock_sha256"),
        ("env/release-era/Dockerfile", "dockerfile_sha256"),
    ):
        actual = digest(read_bytes(relative))
        if actual != release[key]:
            errors.append(
                f"{relative} is {actual} but the preregistered matrix declares "
                f"{release[key]}: the qualified environment and the committed one differ"
            )
    dockerfile = read_bytes("env/release-era/Dockerfile").decode()
    if not re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE):
        errors.append(
            "env/release-era/Dockerfile base image is not digest-pinned; a mutable "
            "tag is not an environment identity (DR-0006)"
        )
    if "--checksum=sha256:" not in dockerfile:
        errors.append(
            "env/release-era/Dockerfile fetches the source archive without a checksum"
        )
    floating = []
    for line in read_bytes("env/release-era/requirements.lock").decode().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("--"):
            continue
        if re.match(r"^[A-Za-z0-9._-]+(\[[^]]+\])?==", stripped):
            continue
        if stripped.startswith("--hash=") or stripped == "\\" or stripped.endswith("\\"):
            continue
        floating.append(stripped)
    floating = [item for item in floating if not item.startswith("--hash=")]
    if floating:
        errors.append(
            "env/release-era/requirements.lock has non-pinned requirement lines: "
            + "; ".join(sorted(floating)[:5])
        )
    if "--require-hashes" not in dockerfile:
        errors.append("env/release-era/Dockerfile installs the lock without --require-hashes")
    return errors


def check_source_gate_precedes_import() -> list[str]:
    """AC2: every reference run validates source hashes before importing torch."""
    errors: list[str] = []
    for relative, call in SOURCE_GATED_WORKERS:
        lines = (ROOT / relative).read_text(encoding="utf-8").splitlines()
        validated = next(
            (
                i
                for i, line in enumerate(lines)
                # The call, not the definition: a gate defined above the import
                # but invoked below it is exactly the ordering this rejects.
                if re.search(rf"\b{call}\(", line) and not line.lstrip().startswith("def ")
            ),
            None,
        )
        imported = next(
            (i for i, line in enumerate(lines) if re.match(r"\s*import torch\b", line)),
            None,
        )
        if validated is None:
            errors.append(f"{relative} no longer calls {call}() to admit a source tree")
            continue
        if imported is not None and imported < validated:
            errors.append(
                f"{relative} imports torch at line {imported + 1}, before {call}() at "
                f"line {validated + 1}: source validation must precede the numerical import"
            )
    return errors


def check_source_isolation() -> list[str]:
    """AC3: the selected commit differs from v1.0.2 by the import patch only."""
    errors: list[str] = []
    comparison = read_json(SOURCE_COMPARISON)
    release_files = comparison["release"]["files"]
    selected_files = comparison["selected"]["files"]
    if set(release_files) != set(selected_files):
        errors.append("source-comparison.json compares different file sets per side")
        return errors
    differing = {name for name in release_files if release_files[name] != selected_files[name]}
    expected = {"torchsynth/synth.py", "torchsynth/profile.py"}
    if differing != expected:
        errors.append(
            "v1.0.2 vs the selected commit differ in "
            + ", ".join(sorted(differing))
            + f"; the recorded isolation claims exactly {sorted(expected)}"
        )
    patch = comparison["patch"]
    if patch["path"] != "torchsynth/synth.py":
        errors.append(f"the recorded patch is on {patch['path']}, not torchsynth/synth.py")
    removed = [
        line
        for line in patch["unified_diff"].splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    added = [
        line
        for line in patch["unified_diff"].splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    if len(removed) != 1 or len(added) != 1:
        errors.append(
            f"the import-only patch changes {len(removed)} removed / {len(added)} added "
            "lines; DR-0001 claims a single import line"
        )
    upstream = read_json(UPSTREAM)
    for name, expected_digest in upstream["files"].items():
        if selected_files.get(name) != expected_digest:
            errors.append(
                f"{UPSTREAM} pins {name} at {expected_digest} but the equivalence "
                f"experiment measured {selected_files.get(name)}"
            )
    equivalence = read_json(SOURCE_EQUIVALENCE)
    if equivalence.get("status") != "passed":
        errors.append(
            f"{SOURCE_EQUIVALENCE} status is {equivalence.get('status')!r}, not 'passed'"
        )
    for name, expected_digest in equivalence["definition_sha256"].items():
        path = ROOT / "env/release-era" / name
        if not path.is_file():
            errors.append(f"{SOURCE_EQUIVALENCE} names env/release-era/{name}, which is missing")
        elif digest(path.read_bytes()) != expected_digest:
            errors.append(
                f"env/release-era/{name} has changed since the #11 equivalence experiment "
                f"recorded it ({expected_digest}); the experiment no longer describes the tree"
            )
    return errors


def check_plan_binding() -> list[str]:
    """The sentinel's frozen expectation is bound to the committed plan.

    ``check_sentinel`` in ``env/release-era/qualify_repeatability.py`` binds the
    active plan to the frozen expectation by ``plan_sha256``, so a plan edit that
    does not re-register the expectation silently detaches the sentinel from
    what it is meant to assert (DR-0009 amendment A2 re-pinned exactly this).
    """
    errors: list[str] = []
    matrix = read_json(MATRIX)
    record = read_json(PUBLICATION)
    actual = digest(canonical_json_bytes(matrix))
    declared = record["sentinel"]["plan_sha256"]
    if actual != declared:
        errors.append(
            f"{MATRIX} hashes to {actual} but the frozen sentinel in {PUBLICATION} "
            f"expects plan {declared}: the plan changed without re-registering the "
            "sentinel expectation from a sanctioned run"
        )
    return errors


def check_repeatability_census() -> list[str]:
    """AC4/AC5/AC6: the measured census still says what DR-0006 reports."""
    errors: list[str] = []
    matrix = read_json(MATRIX)
    record = read_json(PUBLICATION)
    if record.get("status") != "PASS":
        errors.append(f"{PUBLICATION} status is {record.get('status')!r}, not PASS")
    if record.get("source_commit") != matrix["source_commit"]:
        errors.append("the publication and the preregistered plan name different sources")

    cells = record["cells"]
    if len(cells) != 128 or any(cell["status"] != "PASS" for cell in cells):
        refused = sorted({cell["status"] for cell in cells} - {"PASS"})
        errors.append(
            f"expected 128 completed cells, measured {len(cells)} with statuses {refused}; "
            "an absent or refused cell is NO_VERDICT, never evidence (DR-0006 drift policy)"
        )
    for size in matrix["batch_sizes"]:
        measured = sum(1 for cell in cells if cell["batch_size"] == size)
        if measured != 32:
            errors.append(f"batch size {size} has {measured} cells, expected 32")
    if {cell["runtime"] for cell in cells} != set(matrix["runtime_definitions"]):
        errors.append("the measured runtimes do not match the declared runtime definitions")

    counts: dict[tuple[str, str], int] = {}
    for comparison in record["comparisons"]:
        key = (comparison["kind"], comparison["status"])
        counts[key] = counts.get(key, 0) + 1
    # AC4 fresh-process repeats, AC5 batch independence, AC6 retained disagreement.
    for key, expected in (
        (("repeat", "PASS"), 64),
        (("batch", "PASS"), 48),
        (("cross_runtime", "FAIL"), 32),
    ):
        if counts.get(key, 0) != expected:
            errors.append(
                f"{key[0]} comparisons with status {key[1]}: measured {counts.get(key, 0)}, "
                f"expected {expected}"
            )
    if any(kind == "repeat" and status != "PASS" for kind, status in counts):
        errors.append("a fresh-process repeat comparison is not byte-exact")
    if any(kind == "batch" and status != "PASS" for kind, status in counts):
        errors.append("a batch-size comparison is not byte-exact")
    if not any(kind == "cross_runtime" for kind, _ in counts):
        errors.append(
            "no cross-runtime comparison is retained; disagreeing cross-environment "
            "results must be preserved, never dropped (issue #3 non-goals)"
        )
    for comparison in record["comparisons"]:
        if comparison["kind"] != "cross_runtime":
            continue
        if not comparison.get("artifacts"):
            errors.append("a retained cross-runtime disagreement has no measured artifacts")
            break
    if "historical_baseline" not in record:
        errors.append(
            "the original 128-cell baseline is no longer retained; DR-0006 requires the "
            "superseded profile's results to survive the transition"
        )

    declared_cases = {case["name"]: case["index"] for case in matrix["cases"]}
    for name, index in EXPECTED_CASES.items():
        if declared_cases.get(name) != index:
            errors.append(f"preregistered case {name} is not index {index}")
    if not {"normalization-on", "normalization-off"} <= set(declared_cases):
        errors.append("the matrix no longer exercises both normalization branches")
    # synth1B1-312-6 is the upstream name of global 39942 at the nominal batch
    # size of 128; issue #3 names both spellings of the same identity.
    if divmod(39942, 128) != (312, 6):
        errors.append("global 39942 is not synth1B1-312-6 at the nominal batch size")
    return errors


def check_decision_records() -> list[str]:
    """AC7: a decision record names the environment and bounds portability."""
    errors: list[str] = []
    dr0001 = (ROOT / "spec/decision-records/0001-torchsynth-version.md").read_text(
        encoding="utf-8"
    )
    dr0006 = (ROOT / "spec/decision-records/0006-canonical-runtime.md").read_text(
        encoding="utf-8"
    )
    dr0007 = (ROOT / "spec/decision-records/0007-single-sound-execution.md").read_text(
        encoding="utf-8"
    )
    if "Status: Accepted" not in dr0006:
        errors.append("DR-0006 is no longer Accepted")
    if "## Permitted host scope" not in dr0006:
        errors.append(
            "DR-0006 no longer bounds its host scope; naming a canonical environment "
            "without bounding portability does not satisfy issue #3"
        )
    if "release-mkl-compatible-v1" not in dr0006:
        errors.append("DR-0006 no longer names the canonical profile")
    if "DR-0006" not in dr0001 or "DR-0007" not in dr0001:
        errors.append(
            "DR-0001 does not point forward to DR-0006/DR-0007; its 'Remaining "
            "qualification' section is resolved and must say where"
        )
    if "Reconciliation with DR-0006" not in dr0007:
        errors.append("DR-0007 no longer records its reconciliation against DR-0006")
    index = (ROOT / "spec/decision-records/README.md").read_text(encoding="utf-8")
    for record in ("0006", "0007"):
        if f"({record}-" not in index:
            errors.append(f"DR-{record} is missing from the decision-record index")
    return errors


def check_ci_sentinels() -> list[str]:
    """AC8: a lightweight CI sentinel fails on drift without the full corpus.

    Text checks on purpose: ``.github/workflows/ci.yml``, which runs this module,
    installs no YAML parser, and the wiring being guarded is a small
    line-oriented shape (same reasoning as ``tests/test_ci_lane_wiring.py``).
    """
    errors: list[str] = []
    for relative, invocations in (
        (
            ".github/workflows/reference-repeatability.yml",
            ("env/release-era/qualify_repeatability.sh sentinel",),
        ),
        (
            ".github/workflows/reference-scalar.yml",
            (
                "env/release-era/qualify_scalar.sh --sentinel",
                "env/release-era/qualify_scalar.py verify",
            ),
        ),
    ):
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"{relative} is missing; issue #3 requires a CI drift sentinel")
            continue
        text = path.read_text(encoding="utf-8")
        if "pull_request" not in text:
            errors.append(f"{relative} no longer runs on pull_request")
        if not re.search(r"push:\s*\n\s*branches:\s*\[\s*main\s*\]", text):
            errors.append(f"{relative} no longer runs on push to main")
        for invocation in invocations:
            if invocation not in text:
                errors.append(
                    f"{relative} no longer invokes {invocation!r}: a schema-only record "
                    "check is insufficient (DR-0006 drift policy)"
                )
    return errors


CHECKS = (
    ("publication pins", check_publication_pins),
    ("dispatch profile sites", check_dispatch_profile_sites),
    ("environment definition", check_environment_definition),
    ("source gate ordering", check_source_gate_precedes_import),
    ("source isolation", check_source_isolation),
    ("plan binding", check_plan_binding),
    ("repeatability census", check_repeatability_census),
    ("decision records", check_decision_records),
    ("CI sentinels", check_ci_sentinels),
)


def check() -> list[str]:
    """Every consistency failure across the four #3 work units, in order."""
    errors: list[str] = []
    for name, function in CHECKS:
        errors.extend(f"[{name}] {message}" for message in function())
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    errors = check()
    for message in errors:
        print(message, file=sys.stderr)
    if errors:
        print(f"{len(errors)} consolidation check(s) failed", file=sys.stderr)
        return 1
    print(f"{len(CHECKS)} consolidation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
