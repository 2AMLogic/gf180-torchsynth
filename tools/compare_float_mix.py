#!/usr/bin/env python3
"""Compare the independent float mix chain with the release-era capture.

Stdlib-only. Two modes:

- Store-free (default): verifies the committed digest bindings in
  ``sim/reference/trace-capture.json`` against the directed normalization
  targets and the model's peak/derived-gain rules, and prints the committed
  bypass/normalize digest relations. Exits nonzero on any mismatch.
- ``--store DIR``: additionally loads the captured ``f32le`` buffers for each
  directed normalization case and compares the model chain, rendered from the
  captured inputs, against the captured owned traces byte-for-byte. The VCA
  sites and the scalar peak/gain facts are IEEE-deterministic on identical
  inputs and must match byte-exactly; the two mix traces may drift by roughly
  one ulp where the pinned batched GEMM fused an FMA across the three-term
  reduction, so ``--mix-limit`` may declare the preregistered paired
  ``max_abs_error`` limit from the capture-run calibration (no default: a
  limit that was not preregistered cannot be assumed).

global-0/global-6 are reported as skipped under ``--store``: their physical
mixer levels have no committed provenance (the corpus resolver owns them).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice import float_mix as fm  # noqa: E402

RECORD_PATH = ROOT / "sim/reference/trace-capture.json"
DIRECTED_PATH = ROOT / "spec/reference/directed-voice-v1.json"
DIRECTED_CASES = (
    "normalization:above",
    "normalization:below",
    "normalization:tie",
)
INPUT_TRACES = (
    "vco_1.raw",
    "vco_2.raw",
    "noise.raw",
    "control_upsample.vco_1_amp",
    "control_upsample.vco_2_amp",
    "control_upsample.noise_amp",
)
LEVEL_NAMES = ("mixer.vco_1", "mixer.vco_2", "mixer.noise")
BYTE_EXACT_TRACES = (
    "vco_1.post_vca",
    "vco_2.post_vca",
    "noise.post_vca",
    "mixer.peak",
    "mixer.gain",
)
MIX_TRACES = ("mixer.pre_normalization", "mixer.output")


def f32(x):
    return fm.f32(x)


def load_record():
    return json.loads(RECORD_PATH.read_bytes())


def load_directed():
    return json.loads(DIRECTED_PATH.read_bytes())


def find_case(record, case_id):
    for case in record["cases"]:
        if case["case"]["id"] == case_id:
            return case
    raise SystemExit("capture record has no case " + case_id)


def find_trace(case, name):
    for entry in case["full_inventory"]:
        if entry["name"] == name:
            return entry
    raise SystemExit("case inventory has no trace " + name)


def find_directed(document, case_id):
    for case in document["cases"]:
        if case["id"] == case_id:
            return case
    raise SystemExit("directed manifest has no case " + case_id)


def resolved_levels(document, case_id):
    """Observed physical mixer levels for one directed case.

    The directed record carries the base patch physical map plus per-case
    physical overrides; both are committed evidence, so the levels resolve
    without any runtime conversion being reimplemented here.
    """

    levels = {}
    for name in LEVEL_NAMES:
        levels[name] = document["base"][name]["physical"]
    for name, override in find_directed(document, case_id)["overrides"].items():
        if name in levels:
            levels[name] = override["physical"]
    return [levels[name] for name in LEVEL_NAMES]


def sha256_of_bytes(data):
    return hashlib.sha256(data).hexdigest()


def check_record_bindings(record, document):
    failures = []
    print("== committed digest bindings (store-free)")
    for case_id in DIRECTED_CASES:
        case = find_case(record, case_id)
        target = f32(find_directed(document, case_id)["normalization_target"]["target_peak"])
        peak_entry = find_trace(case, "mixer.peak")
        gain_entry = find_trace(case, "mixer.gain")
        peak_digest = hashlib.sha256(
            fm.f32le_bytes([target])
        ).hexdigest()
        gain_digest = hashlib.sha256(
            fm.f32le_bytes([fm.derived_gain(target)])
        ).hexdigest()
        for label, computed, recorded in (
            ("peak", peak_digest, peak_entry["sha256"]),
            ("gain", gain_digest, gain_entry["sha256"]),
        ):
            status = "PASS" if computed == recorded else "FAIL"
            if status == "FAIL":
                failures.append(case_id + " " + label)
            print(
                "  %-22s %-4s %s: model %s vs captured %s"
                % (case_id, status, label, computed[:16], recorded[:16])
            )
        branch = case["normalization_branch"]
        expected_branch = target > 1.0
        if branch["peak_gt_one"] is not expected_branch:
            failures.append(case_id + " branch")
            print("  %-22s FAIL  recorded branch disagrees with peak > 1" % case_id)
        pre = find_trace(case, "mixer.pre_normalization")["sha256"]
        out = find_trace(case, "mixer.output")["sha256"]
        relation = "bypass-preserves" if pre == out else "division-applied"
        expected_relation = "bypass-preserves" if target <= 1.0 else "division-applied"
        status = "PASS" if relation == expected_relation else "FAIL"
        if status == "FAIL":
            failures.append(case_id + " relation")
        print(
            "  %-22s %-4s %s %s"
            % (case_id, status, relation, pre[:16])
        )
    tie = find_case(record, "normalization:tie")
    pre = find_trace(tie, "mixer.pre_normalization")["sha256"]
    lane = find_trace(tie, "vco_1.post_vca")["sha256"]
    status = "PASS" if pre == lane else "FAIL"
    if status == "FAIL":
        failures.append("tie lane identity")
    print("  %-22s %-4s tie mix equals vco_1.post_vca lane" % ("normalization:tie", status))
    exercise = record["normalization_exercise"]
    for name in ("above", "at-boundary", "below", "late-peak", "silence", "tied-maximum"):
        entry = exercise.get(name)
        if entry is None:
            failures.append("exercise " + name)
            print("  exercise %-12s FAIL  missing" % name)
            continue
        ok = (
            entry["derived_gain_matches_rule"]
            and entry["output_matches_original_branch"]
            and entry["division_applied"] == (name in ("above", "late-peak"))
        )
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures.append("exercise " + name)
        print("  exercise %-12s %s" % (name, status))
    return failures


def paired_max_abs_error(reference, candidate):
    return max(abs(a - b) for a, b in zip(reference, candidate))


def compare_with_store(record, document, store, mix_limit):
    failures = []
    print("== stored-buffer comparison (captured inputs -> model chain)")
    for case_id in DIRECTED_CASES:
        case = find_case(record, case_id)
        buffers = {}
        missing = []
        for name in INPUT_TRACES:
            path = store / find_trace(case, name)["file"]
            if not path.is_file():
                missing.append(path.name)
                continue
            buffers[name] = fm.f32le_values(path.read_bytes())
        if missing:
            print("  %-22s SKIP  store missing: %s" % (case_id, ", ".join(missing)))
            continue
        levels = resolved_levels(document, case_id)
        rendered, diagnostics = fm.render_mix_chain(
            (buffers["vco_1.raw"], buffers["vco_2.raw"], buffers["noise.raw"]),
            (
                buffers["control_upsample.vco_1_amp"],
                buffers["control_upsample.vco_2_amp"],
                buffers["control_upsample.noise_amp"],
            ),
            levels,
        )
        for name in BYTE_EXACT_TRACES:
            path = store / find_trace(case, name)["file"]
            if not path.is_file():
                print("  %-22s SKIP  store missing: %s" % (case_id, path.name))
                continue
            captured = path.read_bytes()
            candidate = fm.f32le_bytes(rendered[name])
            status = "PASS" if candidate == captured else "FAIL"
            if status == "FAIL":
                failures.append(case_id + " " + name)
            print("  %-22s %-4s %s (byte-exact)" % (case_id, status, name))
        for name in MIX_TRACES:
            path = store / find_trace(case, name)["file"]
            if not path.is_file():
                print("  %-22s SKIP  store missing: %s" % (case_id, path.name))
                continue
            captured = fm.f32le_values(path.read_bytes())
            candidate = rendered[name]
            if candidate == captured:
                print("  %-22s PASS  %s (byte-exact)" % (case_id, name))
                continue
            error = paired_max_abs_error(captured, candidate)
            if mix_limit is not None and error <= mix_limit:
                print(
                    "  %-22s PASS  %s within declared mix limit (%.3g <= %.3g)"
                    % (case_id, name, error, mix_limit)
                )
                continue
            failures.append(case_id + " " + name)
            print(
                "  %-22s FAIL  %s drift %.3g%s"
                % (case_id, name, error, "" if mix_limit is None else " > %.3g" % mix_limit)
            )
        out_entry = find_trace(case, "mixer.output")
        path = store / out_entry["file"]
        if path.is_file():
            audio_digest = case["modes"]["full"]["audio_sha256"]
            if audio_digest != out_entry["sha256"]:
                failures.append(case_id + " audio digest")
                print(
                    "  %-22s FAIL  record audio digest disagrees with mixer.output"
                    % case_id
                )
    for case_id in ("global-0", "global-6"):
        print(
            "  %-22s SKIP  no committed mixer-level provenance (corpus resolver owns it)"
            % case_id
        )
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        type=Path,
        default=None,
        help="operator store directory holding the captured f32le case files",
    )
    parser.add_argument(
        "--mix-limit",
        type=float,
        default=None,
        help="preregistered paired max_abs_error limit for the two mix traces",
    )
    args = parser.parse_args(argv)
    record = load_record()
    document = load_directed()
    failures = check_record_bindings(record, document)
    if args.store is not None:
        failures += compare_with_store(record, document, args.store, args.mix_limit)
    if failures:
        print("FAIL: %d mismatch(es): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("OK: all committed bindings verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
