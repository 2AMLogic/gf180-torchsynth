# Scorecard contract v1

This contract implements the reporting boundary of
[DR-0004](decision-records/0004-verification-claims.md) and the
[measurement plan](../docs/MEASUREMENT-PLAN.md). It defines representation and
validation, not estimators, numeric acceptance limits, or a release rubric.

The normative TorchSynth commit remains
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`. The product renders one four-second
44.1 kHz default-nebula sound per trigger. A resolved scalar execution may use
`batch_size=1, reproducible=False`; canonical batched capture is a qualification
fixture protocol, not a device batching requirement. This API imposes no batch
shape and changes no source, noise, timing, preparation, or normalization policy.

## Row fields

The [row JSON Schema](schemas/scorecard-row-v1.schema.json) uses JSON Schema
2020-12. Every field below is mandatory; unknown fields are rejected. All text
must contain a non-whitespace character. Versions and identities are explicit
strings, not inferred from filenames or package defaults.

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`; changing the wire contract requires a new version. |
| `case` | `id` and `partition` (`development` or `holdout`). This is the measurement partition, not upstream synth1B1 train/test membership. |
| `trace`, `property` | Explicit names supplied by the producer; no positional ordering. |
| `estimator` | `name` and `version`, identifying the implementation/configuration used. |
| `rubric` | `id` and `version`, identifying the decision procedure and coverage rules. |
| `unit` | Native unit of the scalar row; use `1` for dimensionless values. No conversion is performed. |
| `expected` | `value` (number or null) and nonblank `source`. |
| `observed` | Finite scalar measurement, or null when refused/missing. |
| `tolerance` | Nonnegative `value` (number or null) and nonblank `source`. The named rubric defines how it is applied; no limit is supplied by this API. |
| `validity` | `status` and an explicit `reason`, including for valid measurements. |
| `coverage` | `complete`, `partial`, or `none` for the evidence required by this row. |
| `verdict` | Exactly one of the four states below. |
| `artifact` | Opaque `identity` and lowercase 64-digit `sha256`. |

Version 1 supports scalar numbers, not vectors, booleans, numeric strings, or
arbitrary nested measurement payloads. Split independent values into separate
rows with appropriate property/unit names. Numbers must be finite and within
the binary64 range; that bound is a serialization constraint, not a fidelity
tolerance. Booleans are not numbers. NaN and Infinity are not JSON results.

## State invariants

| Verdict | Validity status | Observation | Coverage |
| --- | --- | --- | --- |
| `PASS` | `valid` | Finite number | `complete` |
| `FAIL` | `valid` | Finite number | `complete` |
| `NO VERDICT` | `invalid` or `insufficient` | null | Any declared coverage |
| `MISSING EVIDENCE` | `missing` | null | `none` |

PASS/FAIL also require non-null expected and tolerance values. `valid` means the
producer asserts the estimator, preparation, and required evidence are qualified
for this row. Its reason and artifact must substantiate that assertion. The API
checks consistency of declarations; it does not run the estimator or reproduce
the rubric's decision. Schema validity alone is not measurement validity.

NO VERDICT means an attempted evaluation cannot support a decision, for example
an unqualified estimator, unstable pitch, preparation failure, or an error floor
above the required resolution. MISSING EVIDENCE means the required input or
capture is absent. It is a distinct refusal state, never a numerical failure or
pass. Both need a concrete `validity.reason`; neither may expose an `observed`
number, including zero. Rejected estimates can be retained in diagnostic
artifacts, without becoming accepted scorecard values.

Unavailable expected/tolerance values are explicit nulls with the fields still
present. If a source is not available, state why in `source`, for example
`unavailable: rubric not frozen`. Only refusal states allow these nulls. A known
expected zero or tolerance zero remains a real number; null is never coerced.

Coverage counts evidence availability, independently of success. A complete
capture can still produce NO VERDICT if its estimator is invalid. Partial
coverage cannot issue PASS or FAIL in v1. Define a narrower row explicitly if a
qualified partial-window measurement is needed; do not silently change coverage.

## Artifact boundary

The artifact object deliberately has no dependency on issue #13's artifact
validator. `identity` is an opaque portable identifier, not a local pathname;
`sha256` identifies the bytes of the referenced record. Producers should use a
record bundling the input/output/provenance and any qualification diagnostics.
For missing evidence, reference an existing case/request or diagnostic record
that records what is missing. Never invent a digest for an absent capture.

The scorecard validates reference syntax only. It neither resolves the identity
nor hashes files, verifies provenance, or proves that the record exists. A later
integration adapter can resolve and validate these references through the
artifact API without changing this boundary. Failed artifact validation must
produce a refusal, not preserve a measured verdict. Tests use explicitly
synthetic records; their results establish contract behavior only.

## Reports and API

The [report JSON Schema](schemas/scorecard-report-v1.schema.json) requires
`schema_version`, `partition`, `rubric`, `rows`, and `summary`.
`torchsynth_voice.scorecard` provides:

- `validate_row(row)` and `validate_report(report)`: raise `ScorecardError`
  (a `ValueError`) on invalid data; return None on success.
- `row_from_json(text)` / `row_to_json(row)` and the corresponding
  `report_from_json` / `report_to_json`: validate on every read/write. Readers
  reject duplicate keys, NaN/Infinity, and numeric overflow. Writers use strict
  JSON and retain nulls.
- `make_report(rows, *, partition, rubric)`: copy raw rows, preserve order, and
  compute independent counts. All rows must match the named partition and
  rubric, including its version. These identities are mandatory even for an
  empty report.
- `summarize_rows(rows)`: count a single comparison family. It rejects different
  partitions, properties, units, estimator names/versions, or rubric
  identities/versions. Cases and traces may differ. There is no averaging API.

The summary contains `total_rows`, `verdicts` (one count for each of the four
states), and `coverage` (one count for each coverage state). The two sets of
counts are independent; a failure does not subtract evidence coverage. Report
counts can inventory heterogeneous properties, but never combine their observed
values. All raw rows remain available. Counts describe submitted rows, not
unique cases or completeness against an external case inventory; omitted rows
cannot be detected without that inventory. Empty reports count nothing.

Reports have no product verdict, weighted score, or implicit acceptance from
zero failures. Mandatory-row selection, expected-case coverage denominators,
and a frozen release rubric belong to later qualification work. Altering an
estimator, tolerance, or coverage rule requires a new rubric version; producers
must keep development and holdout reports separate.

JSON Schema validates row structure/state and report structure/partition
isolation. Register both local schema documents under their `$id` URNs when
using a standard validator; no network lookup is needed. Full report validation
also requires the Python API's cross-row rubric equality and exact recomputation
of summary counts. Standard JSON Schema cannot express those relationships.
The API additionally requires Python integers for versions/counts rather than
integer-valued floats. Parse strict JSON before any external schema validation;
some libraries accept non-JSON NaN objects when validating in-memory values.

For example, given a validated row dictionary:

```python
from torchsynth_voice.scorecard import make_report, report_to_json

report = make_report(
    [row], partition=row["case"]["partition"], rubric=row["rubric"]
)
encoded = report_to_json(report)
```

## Verification evidence

`tests/test_scorecard.py` exercises synthetic PASS, FAIL, NO VERDICT, and
MISSING EVIDENCE records and negative mutations of provenance, state, numeric,
partition, and aggregation fields. It runs with the standard library under
`python3 -m unittest discover -s tests -v`; no TorchSynth, NumPy, or PDK tools
are imported. The tests make no claim about measured TorchSynth/RTL fidelity,
hardware playback, synthesis, or physical implementation.

Builder evidence recorded 2026-09-18 (base `f9f95e3`):

| Check | Observed result |
| --- | --- |
| `python3 -m unittest discover -s tests -v` (Python 3.14.7) | 34 tests passed, zero failures/skips; 28 scorecard tests and 6 existing tests. |
| Same suite using the operator's read-only Python 3.13.2 environment | 34 tests passed, zero failures/skips. |
| `python3 tools/check_contract.py` (also Python 3.13.2) | `contract manifests are internally consistent`; exit 0. |
| `uv run --no-project --with jsonschema python tests/test_scorecard.py --schemas -v` (Python 3.11.5, jsonschema 4.25.1) | 28 tests passed with independent Draft 2020-12 validation of the row mutations and report structure, plus both schema meta-checks. |
| `python3 -m compileall -q src tests tools` | Exit 0. |
| `ruff format --check src/torchsynth_voice/scorecard.py tests/test_scorecard.py` and `ruff check` on those files (0.12.7) | Both files formatted; all lint checks passed. |

The initial 25 scorecard tests were written before the module and failed with
`ModuleNotFoundError: No module named 'torchsynth_voice.scorecard'`. They passed
after implementation; three further defensive tests and the opt-in schema
parity checks were added afterward. The optional schema run uses an isolated
tool environment; normal CI has no added dependency or skipped schema test.
