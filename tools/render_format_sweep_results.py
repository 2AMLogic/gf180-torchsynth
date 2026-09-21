#!/usr/bin/env python3
"""Render the spec doc Results section from the sweep evidence JSON."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
doc = json.loads((ROOT / "sim" / "reference" / "control-format-sweep-v1.json").read_text())

lines = []
lines.append("Executed: `timeout 7200 python3 tools/sweep_control_path_formats.py")
lines.append("--directed-stride 4 --draws 32` — %d fixtures (%d directed stride-4 +"
             % (len(doc["candidates"]) and doc["preregistration"]["directed_used"] + doc["preregistration"]["draws_used"],
                doc["preregistration"]["directed_used"]))
lines.append("%d corpus-style draws), %d candidates, elapsed %.0f s."
             % (doc["preregistration"]["draws_used"], len(doc["candidates"]),
                doc["receipts"]["elapsed_seconds"]))
lines.append("")
lines.append("Per-trace rows (max abs, argmax fixture, pooled RMSE, worst SNR,")
lines.append("saturation/rounding counter totals, per-row verdict) are in the evidence")
lines.append("JSON. Summary:")
lines.append("")

by_axis = {}
for cand in doc["candidates"]:
    by_axis.setdefault(cand["axis"], []).append(cand)

order = ["baseline"] + [axis for axis in doc["preregistration"]["axes"] if axis != "baseline"]
lines.append("| Axis | Candidate | Worst-row max abs | Rows PASS | All rows | Certified full |")
lines.append("| --- | --- | --- | --- | --- | --- |")
for axis in order:
    for cand in by_axis.get(axis, []):
        worst = max(row["max_abs"] for row in cand["rows"].values())
        passing = sum(1 for row in cand["rows"].values() if row["verdict"] == "PASS")
        ident = cand["identity"]
        ident_short = ident.replace("ctrl=Q2.21 pitch=Q2.21 upfrac=uQ.31 lut=4096x24", "...")
        lines.append(
            "| `%s` | `%s` | %.3e | %d/22 | %s | %s |"
            % (axis, ident_short, worst, passing,
               "PASS" if cand["all_rows_pass"] else "**FAIL**",
               "yes" if cand["certified_full_length"] else "grid")
        )
lines.append("")
lines.append("Axis winners (smallest-footprint band-passing member per axis):")
lines.append("")
lines.append("| Axis | Baseline passes (all 22 rows) | Smallest passing member |")
lines.append("| --- | --- | --- |")
for axis, winner in doc["axis_winners"].items():
    lines.append(
        "| `%s` | %s | `%s` |"
        % (axis, "yes" if winner["baseline_all_rows_pass"] else "no",
           (winner["smallest_passing_member"] or "none passing").replace(" ctrl=", ", ctrl=").replace(" pitch=", ", pitch=").replace(" upfrac=", ", up=").replace(" lut=", ", lut=").replace(" mode=", ", mode="))
    )
lines.append("")
lines.append("Recommendation: `%s` composed baseline — %s; every recommendation is"
             % (doc["recommendation"]["composed_identity"],
                "all rows PASS" if doc["recommendation"]["baseline_all_rows_pass"]
                else "NOT supported by this run"))
lines.append("**CANDIDATE-pending-#53-ratification, never accepted**; refusals recorded:"
             " %d; holdout reads: %d; committed corpus cases read: %d."
             % (len(doc["refusals"]),
                doc["preregistration"]["holdout_reads"],
                doc["preregistration"]["committed_corpus_cases_read"]))

print("\n".join(lines))
