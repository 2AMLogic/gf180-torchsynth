"""Host-gated actual-Voice mutation-runtime publication and stdlib checks.

Default invocation performs the DR-0006 gated release-era qualification for
issue 30's voice-runtime bridge: it resolves the bounded mutation schedules
with the stdlib contract (``mutations.make_plan`` +
``mutation_runtime.resolve_voice_plan``), rebuilds the unchanged release
image, runs the Python 3.9 worker ``env/release-era/mutation_worker.py``
offline inside it, re-validates every recorded event log against its plan
with the landed contract, binds ``mu1-`` evidence envelopes, and writes the
bounded publication ``sim/reference/mutation-runtime-v1.json``. It never
reuses an existing output directory.

``--check-publication`` is the stdlib-only mode for ordinary CI hosts: it
re-verifies the committed publication against the current contract, plans,
schedules, event logs, envelopes and input digests without Torch or Docker.
Absence is reported as absent, never as a pass; staleness fails.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import mutations, mutation_runtime  # noqa: E402
from torchsynth_voice import trace_capture, trace_registry  # noqa: E402

PUBLICATION_PATH = ROOT / "sim/reference/mutation-runtime-v1.json"
WORKER_PATH = ROOT / "env/release-era/mutation_worker.py"
PROTOTYPE_PATH = ROOT / "sim/reference/trace-registry-prototype.json"
INVENTORY_PATH = ROOT / "spec/reference/parameter-inventory-v1.json"
BATCH_SIZE = 32
SOUND_INDEX = 0
CASE_ORDER = (
    "empty",
    "sham",
    "upstream_zero",
    "final_zero",
    "parameter_half",
    "composed_crash",
)
STALENESS_INPUTS = (
    "src/torchsynth_voice/mutations.py",
    "src/torchsynth_voice/mutation_runtime.py",
    "src/torchsynth_voice/trace_capture.py",
    "src/torchsynth_voice/trace_registry.py",
    "spec/reference/mutation-seams-v1.json",
    "spec/reference/trace-registry-v1.json",
    "spec/reference/parameter-inventory-v1.json",
    "sim/reference/trace-registry-prototype.json",
    "env/release-era/mutation_worker.py",
)
NOT_RUN = (
    "fault-operator families #31/#32/#33 (they register through this API in "
    "their own issues; the bridge.* voice-runtime operators are test-only "
    "bridge proofs, never family operators)",
    "detector qualification #34 matrix publication",
    "voice.normalization_decision injection (not writable without the named "
    "AudioMixer.output producer handoff; the fail-closed refusal is executed "
    "host-side instead)",
    "development corpus cases and batch slots beyond the pinned global-0 "
    "selected sound",
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def write_json(path, value):
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )


def parameter_names():
    inventory = json.loads(INVENTORY_PATH.read_bytes())
    return sorted(parameter["name"] for parameter in inventory["parameters"])


def voice_binding():
    prototype = json.loads(PROTOTYPE_PATH.read_bytes())
    return {
        "case_id": "global-0",
        "partition": "development",
        "fixture_identity": prototype["executions"]["captured"]["audio_sha256"],
    }


def voice_plans(catalog, document):
    """Deterministic host-resolved plans for the bounded runtime matrix."""
    binding = voice_binding()
    instance = mutation_runtime.instance
    slot = trace_slot()

    def schedule_for(case):
        if case == "empty":
            mutations_list = []
        elif case == "sham":
            mutations_list = [
                instance(
                    "vm-sham",
                    "bridge.sham_slot",
                    "voice.post_module",
                    configuration={"trace": "mixer.output", "slot": slot},
                )
            ]
        elif case == "upstream_zero":
            mutations_list = [
                instance(
                    "vm-upstream",
                    "bridge.scale_slot",
                    "voice.post_module",
                    0.0,
                    {
                        "trace": "control_upsample.vco_1_amp",
                        "slot": slot,
                    },
                )
            ]
        elif case == "final_zero":
            mutations_list = [
                instance(
                    "vm-final",
                    "bridge.scale_slot",
                    "voice.post_module",
                    0.0,
                    {"trace": "mixer.output", "slot": slot},
                )
            ]
        elif case == "parameter_half":
            mutations_list = [
                instance(
                    "vm-parameter",
                    "bridge.scale_parameter",
                    "voice.parameter_value",
                    0.5,
                    {"parameter": "adsr_1.release", "slot": slot},
                )
            ]
        elif case == "composed_crash":
            mutations_list = [
                instance(
                    "vm-scale",
                    "bridge.scale_slot",
                    "voice.post_module",
                    0.0,
                    {
                        "trace": "control_upsample.vco_1_amp",
                        "slot": slot,
                    },
                ),
                instance(
                    "vm-raise",
                    "bridge.raise_slot",
                    "voice.post_module",
                    configuration={"trace": "vco_1.post_vca", "slot": slot},
                ),
            ]
        else:
            raise ValueError("unknown runtime case: " + case)
        plan = mutations.make_plan(binding, mutations_list, catalog)
        schedule = mutation_runtime.resolve_voice_plan(
            plan, document, parameter_names(), BATCH_SIZE
        )
        return plan, schedule

    return {case: schedule_for(case) for case in CASE_ORDER}


def trace_slot():
    """The pinned selected batch slot for the global-0 case (index 0)."""
    _, slot = divmod(SOUND_INDEX, BATCH_SIZE)
    return slot


def normalization_refusal_row(catalog):
    """Executed host-side refusal for the non-writable decision seam."""
    instance = mutation_runtime.instance
    observed = None
    try:
        mutations.make_plan(
            voice_binding(),
            [
                instance(
                    "vm-norm",
                    "bridge.scale_slot",
                    "voice.normalization_decision",
                    0.0,
                    {"trace": "mixer.output", "slot": trace_slot()},
                )
            ],
            catalog,
        )
    except mutations.MutationError as error:
        observed = str(error)
    if observed is None or "Producer handoff required" not in observed:
        raise SystemExit(
            "FAIL: normalization decision seam was not refused with the "
            "producer handoff"
        )
    return {
        "fault": "normalization-decision-replacement",
        "operator": "bridge.scale_slot",
        "seam": "voice.normalization_decision",
        "downstream": "mutations.validate_plan (fail-closed at plan time)",
        "expected_refusal": "Producer handoff required",
        "observed_refusal": observed[:256],
        "tripped": True,
        "control_accepted": True,
    }


def validate_worker_report(report, document):
    require = trace_registry.require
    require(report["status"] == "PASS", "worker did not pass")
    require(
        report["mutation_runtime_version"] == "mutation-runtime-v1",
        "unknown worker version",
    )
    require(
        report["runtime_profile"] == "release-mkl-compatible-v1",
        "worker outside the qualified runtime profile",
    )
    require(
        report["registry_sha256"]
        == sha256(trace_registry.REGISTRY_PATH.read_bytes()),
        "worker registry identity is stale",
    )
    require(
        report["registry_token"] == trace_registry.registry_token(),
        "worker registry token is stale",
    )
    require(
        report["baseline_sentinel"] == "PASS",
        "worker baseline sentinel missing",
    )
    for name in ("mutations", "mutation_runtime", "trace_capture",
                 "trace_registry"):
        path = "src/torchsynth_voice/" + name + ".py"
        require(
            report["producer_sha256"][path]
            == sha256((ROOT / path).read_bytes()),
            "worker producer digest is stale: " + path,
        )
    attempts = report["attempts"]
    require("plain" in attempts and "clean_rerun" in attempts, "missing attempts")
    for case in CASE_ORDER:
        require(case in attempts, "worker attempt missing: " + case)


def fault_matrix_rows(report, controls):
    attempts = report["attempts"]
    rows = []

    def row(fault, operator, seam, downstream, expected, observed, control):
        rows.append(
            {
                "fault": fault,
                "operator": operator,
                "seam": seam,
                "downstream": downstream,
                "expected_refusal": expected,
                "observed_refusal": observed,
                "tripped": bool(control and observed is not None),
                "control_accepted": bool(control),
            }
        )

    worker_gate = "mutation_worker.py executed assertions (pinned runtime)"
    row(
        "upstream-module-slot-replacement",
        "bridge.scale_slot",
        "voice.post_module",
        "capture inventory + downstream traces + returned audio",
        "declared slot audio diverges; seam capture keeps original bytes; "
        "siblings and undeclared slots byte-identical",
        "executed: seam capture original, vco_1.post_vca consumed "
        "replacement, six sibling traces and 31 slots byte-identical"
        if attempts["upstream_zero"]["events"][0]["status"] == "applied"
        else None,
        controls["plain_baseline_sentinel"],
    )
    row(
        "final-module-slot-replacement",
        "bridge.scale_slot",
        "voice.post_module",
        "capture inventory vs returned audio",
        "mixer.output capture keeps the original decision evidence while "
        "the returned audio consumes the replacement",
        "executed: mixer.output/peak/gain captures original, returned "
        "audio slot replaced" if attempts["final_zero"]["events"][0][
            "status"
        ] == "applied" else None,
        controls["plain_baseline_sentinel"],
    )
    row(
        "parameter-value-replacement",
        "bridge.scale_parameter",
        "voice.parameter_value",
        "named-parameter capture + own trace + returned audio",
        "swapped parameter visible to the graph; own trace and declared "
        "slot audio diverge; sibling trace byte-identical",
        "executed: adsr_1.release capture changed, adsr_1.output changed, "
        "sibling unchanged" if attempts["parameter_half"]["events"][0][
            "status"
        ] == "applied" else None,
        controls["plain_baseline_sentinel"],
    )
    crash = attempts["composed_crash"]
    row(
        "composed-ordered-crash",
        "bridge.scale_slot + bridge.raise_slot",
        "voice.post_module",
        "in-band events + NO VERDICT aggregation + clean rerun",
        "declared-order events (applied, errored); render aborts; clean "
        "rerun byte-identical to baseline",
        "executed: events applied+errored in declared order, render "
        "aborted, clean rerun byte-identical"
        if crash["errored"] is not None
        and [event["status"] for event in crash["events"]]
        == ["applied", "errored"]
        and attempts["clean_rerun"]["batch_audio_sha256"]
        == attempts["plain"]["batch_audio_sha256"]
        else None,
        controls["plain_baseline_sentinel"],
    )
    return rows


def host_run(args):
    require = trace_registry.require
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("unqualified host")
    cpu = subprocess.check_output(
        ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
    ).strip()
    os_version = subprocess.check_output(
        ["sw_vers", "-productVersion"], text=True
    ).strip()
    server = subprocess.check_output(
        [
            "docker",
            "version",
            "--format",
            "{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}",
        ],
        text=True,
    ).strip()
    require(
        cpu == "Apple M5" and os_version == "26.5.1" and server == "linux/arm64 29.7.2",
        "host outside DR-0006 measured scope",
    )
    output = args.output.resolve()
    require(
        not output.exists(),
        "output directory already exists; preserve earlier experiments",
    )
    output.mkdir(parents=True)
    catalog = mutations.load_seam_catalog(
        ROOT / mutations.SEAM_CATALOG_PATH
    )
    document = trace_registry.load_registry()
    plans = voice_plans(catalog, document)

    schedules_dir = output / "schedules"
    schedules_dir.mkdir()
    entries = []
    for case in CASE_ORDER:
        _, schedule = plans[case]
        path = schedules_dir / (case + ".json")
        write_json(path, schedule)
        entries.append(
            {"case": case, "file": case + ".json", "sha256": sha256(path.read_bytes())}
        )
    write_json(
        schedules_dir / "manifest.json",
        {"schema_version": 1, "order": list(CASE_ORDER), "schedules": entries},
    )

    build = [
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "--iidfile",
        str(output / "image-id"),
        "-f",
        str(ROOT / "env/release-era/Dockerfile"),
        str(ROOT),
    ]
    with (output / "build.log").open("w") as log:
        subprocess.run(build, check=True, stdout=log, stderr=subprocess.STDOUT,
                       timeout=1800)
    image = (output / "image-id").read_text().strip()
    image_info = json.loads(
        subprocess.check_output(["docker", "image", "inspect", image])
    )[0]
    require(
        image_info["Architecture"] == "amd64" and image_info["Os"] == "linux",
        "wrong image platform",
    )
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
    ]
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        command += ["--env", name + "=1"]
    command += [
        "--env",
        "MKL_CBWR=COMPATIBLE",
        "--mount",
        "type=bind,src=" + str(ROOT) + ",dst=/repo,readonly",
        "--mount",
        "type=bind,src=" + str(output) + ",dst=/output",
        "--entrypoint",
        "env",
        image,
        "-u",
        "ATEN_CPU_CAPABILITY",
        "python",
        "/repo/env/release-era/mutation_worker.py",
        "--worker",
        "--output",
        "/output",
        "--schedules",
        "/output/schedules",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    (output / "worker.stdout").write_text(result.stdout)
    (output / "worker.stderr").write_text(result.stderr)
    require(
        result.returncode == 0,
        "worker failed; retained stdout/stderr in " + str(output),
    )
    report = json.loads((output / "worker.json").read_bytes())
    validate_worker_report(report, document)

    attempts = report["attempts"]
    controls = {
        "plain_baseline_sentinel": report["baseline_sentinel"] == "PASS",
        "plain_vs_empty_byte_identical": attempts["empty"]["batch_audio_sha256"]
        == attempts["plain"]["batch_audio_sha256"],
        "plain_vs_sham_byte_identical": attempts["sham"]["batch_audio_sha256"]
        == attempts["plain"]["batch_audio_sha256"],
        "empty_plan_events_complete": attempts["empty"]["events"] == [],
        "sham_event_ineffective_marked": attempts["sham"]["events"] == [
            {
                "instance_id": plans["sham"][0]["mutations"][0]["instance_id"],
                "seam": "voice.post_module",
                "status": "ineffective",
                "order": 0,
                "sham": True,
                "detail": attempts["sham"]["events"][0]["detail"],
            }
        ],
        "state_restored_after_every_attempt": all(
            attempts[name]["state_restored"] is True
            for name in ("plain",) + CASE_ORDER
        ),
        "clean_rerun_after_crash_byte_identical": attempts["clean_rerun"][
            "batch_audio_sha256"
        ]
        == attempts["plain"]["batch_audio_sha256"],
    }
    failed = [name for name, okay in controls.items() if not okay]
    if failed:
        raise SystemExit("FAIL: runtime controls failed: " + ", ".join(failed))

    rows = fault_matrix_rows(report, controls)
    rows.append(normalization_refusal_row(catalog))
    untripped = [entry["fault"] for entry in rows if not entry["tripped"]]
    if untripped:
        raise SystemExit(
            "FAIL: runtime faults did not trip their checks: " + ", ".join(untripped)
        )

    artifacts = []
    for name in ["plain"] + list(CASE_ORDER) + ["clean_rerun"]:
        record = attempts[name]
        if record.get("audio_slot_file") is not None:
            data = (output / record["audio_slot_file"]).read_bytes()
            require(
                sha256(data) == record["audio_slot_sha256"],
                "raw audio integrity failure: " + name,
            )
    for name in ("plain", "clean_rerun"):
        record = attempts[name]
        artifacts.append(
            {
                "path": "out/" + record["audio_slot_file"],
                "sha256": record["audio_slot_sha256"],
                "size_bytes": (output / record["audio_slot_file"]).stat().st_size,
            }
        )
    worker_json = (output / "worker.json").read_bytes()
    artifacts.append(
        {
            "path": "out/worker.json",
            "sha256": sha256(worker_json),
            "size_bytes": len(worker_json),
        }
    )

    envelopes = {}
    events_summary = {}
    per_case_controls = {
        "empty": {
            "plain_vs_empty_byte_identical": controls[
                "plain_vs_empty_byte_identical"
            ],
            "empty_plan_events_complete": controls[
                "empty_plan_events_complete"
            ],
        },
        "sham": {
            "plain_vs_sham_byte_identical": controls[
                "plain_vs_sham_byte_identical"
            ],
            "sham_event_ineffective_marked": controls[
                "sham_event_ineffective_marked"
            ],
        },
    }
    case_rows = {
        "upstream_zero": [rows[0]],
        "final_zero": [rows[1]],
        "parameter_half": [rows[2]],
        "composed_crash": [rows[3]],
    }
    baseline_controls = {"plain_baseline_sentinel": True}
    for case in CASE_ORDER:
        plan, _ = plans[case]
        events = attempts[case]["events"]
        events_summary[case] = mutations.validate_events(plan, events)
        envelopes[case] = mutations.make_envelope(
            plan=plan,
            events_summary=events_summary[case],
            artifacts=artifacts,
            controls=per_case_controls.get(case, baseline_controls),
            fault_matrix=case_rows.get(case, []),
            runtime_scope="actual-voice-release-era-pinned-global-0",
            not_run=list(NOT_RUN),
        )

    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=ROOT)
    untracked = (
        subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    untracked_files = {
        name: sha256((ROOT / name).read_bytes()) for name in untracked if name
    }
    publication = {
        "schema_version": 1,
        "kind": "mutation-runtime-v1",
        "status": "PASS",
        "mutation_contract": "mutation-v1",
        "inputs": {
            name: sha256((ROOT / name).read_bytes())
            for name in STALENESS_INPUTS
        },
        "trace_registry_sha256": sha256(
            trace_registry.REGISTRY_PATH.read_bytes()
        ),
        "registry_token": trace_registry.registry_token(),
        "worker": report,
        "plans": {case: plans[case][0] for case in CASE_ORDER},
        "schedules": {case: plans[case][1] for case in CASE_ORDER},
        "events": {case: attempts[case]["events"] for case in CASE_ORDER},
        "events_summary": events_summary,
        "envelopes": [envelopes[case] for case in CASE_ORDER],
        "fault_matrix": rows,
        "controls": controls,
        "counts": {
            "cases": len(CASE_ORDER),
            "attempts": len(attempts),
            "faults": len(rows),
            "tripped": sum(1 for entry in rows if entry["tripped"]),
            "controls_passed": sum(1 for okay in controls.values() if okay),
        },
        "not_run": list(NOT_RUN),
        "host": {
            "cpu": cpu,
            "os_version": os_version,
            "platform": platform.platform(),
            "docker_server": server,
        },
        "launch": {
            "command": command,
            "exit_code": result.returncode,
            "stderr": result.stderr,
            "build_command": build,
            "build_log_sha256": sha256((output / "build.log").read_bytes()),
            "image_id": image,
            "image_layers": image_info["RootFS"]["Layers"],
        },
        "project_git": {
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "dirty": bool(diff or untracked_files),
            "diff_sha256": sha256(diff),
            "untracked_state_sha256": sha256(json_bytes(untracked_files)),
        },
    }
    check_publication_fields(publication)
    write_json(PUBLICATION_PATH, publication)
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(PUBLICATION_PATH),
                "cases": publication["counts"]["cases"],
                "faults": publication["counts"]["faults"],
                "envelope_ids": [
                    envelope["envelope_id"]
                    for envelope in publication["envelopes"]
                ],
            }
        )
    )


def check_publication_fields(publication):
    """Full structural + contract validation of a runtime publication."""
    catalog = mutations.load_seam_catalog(ROOT / mutations.SEAM_CATALOG_PATH)
    document = trace_registry.load_registry()
    require = trace_registry.require
    require(publication["status"] == "PASS", "publication did not pass")
    require(
        publication["kind"] == "mutation-runtime-v1", "unknown publication kind"
    )
    require(
        publication["mutation_contract"] == "mutation-v1", "unknown contract"
    )
    require(
        publication["registry_token"] == trace_registry.registry_token(),
        "publication registry identity is stale",
    )
    require(
        publication["trace_registry_sha256"]
        == sha256(trace_registry.REGISTRY_PATH.read_bytes()),
        "publication registry bytes are stale",
    )
    require(
        publication["worker"]["baseline_sentinel"] == "PASS",
        "publication baseline sentinel missing",
    )
    require(
        publication["worker"]["runtime_profile"] == "release-mkl-compatible-v1",
        "publication is not from the qualified runtime profile",
    )
    fresh_plans = voice_plans(catalog, document)
    for case in CASE_ORDER:
        plan = publication["plans"][case]
        require(
            mutations.plan_identity(plan) == plan["plan_id"],
            case + ": plan identity mismatch",
        )
        mutations.validate_plan(plan, catalog)
        require(
            plan == fresh_plans[case][0],
            case + ": plan drifts from the deterministic resolution",
        )
        schedule = publication["schedules"][case]
        require(
            schedule == fresh_plans[case][1],
            case + ": schedule drifts from the deterministic resolution",
        )
        summary = mutations.validate_events(
            plan, publication["events"][case]
        )
        require(
            summary == publication["events_summary"][case],
            case + ": event summary mismatch",
        )
        envelope = next(
            item
            for item in publication["envelopes"]
            if item["plan_id"] == plan["plan_id"]
        )
        identity = mutations.validate_envelope(envelope)
        require(
            identity == envelope["envelope_id"],
            case + ": envelope identity mismatch",
        )
    rows = publication["fault_matrix"]
    require(
        all(entry["tripped"] and entry["control_accepted"] for entry in rows),
        "publication fault matrix has untripped or uncontrolled rows",
    )
    require(
        all(publication["controls"].values()),
        "publication controls incomplete",
    )
    require(
        publication["counts"]["faults"] == len(rows)
        and publication["counts"]["tripped"] == len(rows),
        "publication counts inconsistent",
    )
    for name, expected in publication["inputs"].items():
        require(
            sha256((ROOT / name).read_bytes()) == expected,
            "stale publication input: " + name,
        )


def check_publication_mode():
    if not PUBLICATION_PATH.exists():
        print(
            "publication: ABSENT — requires the host-gated qualified "
            "release-era run; absence is never reported as a pass"
        )
        raise SystemExit(2)
    publication = json.loads(PUBLICATION_PATH.read_bytes())
    check_publication_fields(publication)
    print(
        json.dumps(
            {
                "status": "PASS",
                "publication": str(PUBLICATION_PATH),
                "cases": publication["counts"]["cases"],
                "faults": publication["counts"]["faults"],
                "attempts": publication["counts"]["attempts"],
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-publication", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "out/mutation-runtime",
        help="fresh output directory for the host-gated qualification run",
    )
    args = parser.parse_args()
    if args.check_publication:
        check_publication_mode()
        return
    host_run(args)


if __name__ == "__main__":
    main()
