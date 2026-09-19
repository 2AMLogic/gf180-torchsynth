# Case registry and evidence board v1

[`reference/case-registry-v1.json`](reference/case-registry-v1.json) is the
editable inventory and comparison policy. The renderer expands **every** case
in its pinned manifest sources; result discovery cannot shrink the denominator.
[`../docs/SCORECARD.md`](../docs/SCORECARD.md) and
[`../docs/scorecard.json`](../docs/scorecard.json) are generated views, never
alternative inputs. The schema is
[`schemas/case-registry-v1.schema.json`](schemas/case-registry-v1.schema.json);
its `$defs.result` and `$defs.measurement` specify the evidence documents.

The upstream remains `2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`. This contract
changes no Voice behavior, arithmetic, noise, normalization, timing, or shared
scorecard/artifact schema. It neither runs estimators nor qualifies their
accuracy. No float-to-fixed acceptance tolerance or hardware PASS is introduced.

## Inventory and initial state

Random/directed **family** and development/holdout **partition** are independent
axes. The random source expands the landed corpus ranges: 96 development and
32 holdout cases, named `random-000000` through `random-000127`. The directed
source is checked with `directed.validate_manifest`; every original case ID
is retained, and `resolve_patch` supplies its resolved fixture identity without
copying editable patches into this registry. All 392 directed cases are
development, as specified by that manifest. Synthetic sources are available
only for explicitly synthetic engines and isolated tests.

Each source pins its exact manifest bytes and published identity. A missing,
corrupt, changed-but-unrepinned, duplicate, or inconsistent allocation refuses
generation rather than dropping cases. A reviewed manifest revision updates the
registry pin; earlier results then become stale. Each `fixture_identity` hashes
the source reference, original case ID, partition, and resolved patch or random
index with `artifacts.canonical_bytes`. Even identical patches with different
directed purposes keep different case identities. Directed cases are never
forced into the unique-global-index corpus-index v1 format.

The initial registry requires framing and exact-equality observation slots at
all 32 landed capture intents, plus five final-output properties: mean error,
error RMS, maximum absolute error, sample-count delta, and SNR. These **69 slots
per case are inventory obligations**, not a release rubric or a claim of trace
capture. Estimator qualification is pending; expected values and tolerances
are null with explicit reasons. Comparison exactness is an observation, not an
implicit floating fidelity requirement. Future qualified estimators, preparation,
candidate source, and numeric limits require an explicit policy/rubric revision.

The registered engine is the still-unimplemented integrated RTL. Its source
and preparation file lists are empty deliberately. Empty lists cannot support
current attempted results. Existing runtime smoke and source-qualification
records are not silently imported as per-case hardware evidence. The initial
board has 488 inspected development cases, all NOT RUN, and 32 sealed holdout
allocations with **unknown** outcomes. This is not batch-1 identity, Voice
fidelity, holdout qualification, synthesis, layout, signoff, or playback evidence.

## One-result-per-case layout

```text
evidence/scorecard-v1/
  development/
    case-<sha256(UTF-8 original case ID)>/
      result.json
      measurement.json
      ... referenced captures or render metadata/payloads ...
  holdout/
    case-<sha256(UTF-8 original case ID)>/
      result.json
      ... sealed contents ...
```

`case_directory(registry, case, root)` computes the portable directory name;
opaque directed IDs containing colons/arrows are not filesystem path components.
There is exactly one authoritative `result.json` per case in the selected
registry revision. Create an attempt directory before measuring. An absent
directory means NOT RUN; an existing directory with absent, unreadable, or
corrupt `result.json` means attempted but NO VERDICT. Never publish partial
results as success. This reader supplies no evidence-writing API, archiving
policy, or runtime artifact publisher.

The result requires all of:

- Version, full case identity/family/partition, rubric ID/version, reference
  identity, candidate implementation engine/identity.
- Exact argument-vector `command`, full `configuration` and its canonical-byte
  `configuration_sha256`, partition access
  record, covered-input byte hashes, and the composite `fingerprint`.
- Artifact references with kind, identity, exact-file SHA-256, and a relative
  locator beneath this case directory; strict scorecard v1 rows and a declared
  outcome of PASS, FAIL, or NO VERDICT.

The outcome is rederived and must agree; producers cannot turn a failed row
into PASS by editing the envelope. NOT RUN and STALE are **board result
envelope** outcomes only. The four-state scorecard v1 enum is unchanged.
Result file bytes are independently hashed into the board's `result_sha256`.
Unknown fields, duplicate JSON keys, nonfinite numbers, and invalid row states
are rejected. JSON/API versions and counts require integers, not booleans or
integral float tokens.

## Evidence resolution and numerical decisions

Rows reference `case-measurement` records using identity `cm1-<sha256>`; the
hash covers **exact stored bytes**, including whitespace and final newline.
The record binds the case/partition and input fingerprint and contains raw
measurements keyed by trace/property/unit/estimator. It contains no reference
to its own digest, so there is no self-hash cycle. Missing/invalid/insufficient
measurements have null values; valid raw measurements must be finite numbers.
Missing measurements have coverage `none`. Each required row key also binds
the registry rubric ID/version; expected/tolerance values and their sources
come exclusively from the registry policy.

For a valid raw value, complete coverage, and two declared numeric limits,
`measurement_rows` applies the inclusive absolute interval
`abs(observed - expected) <= tolerance` using exact rational representations
of the recorded numbers. This is the same decision rule supported by the
landed paired metric rubrics. Without declared limits or complete coverage,
the accepted row refuses with `observed: null`; it never substitutes zero.
The raw measurement remains in its hashed diagnostic. Submitted rows must
equal the rows rederived from those verified records, including verdict,
limits, unit, validity reason, estimator, and artifact reference.

Measurement records may reference reference/candidate sample captures by trace, with
explicit encoding (`f32le` or `f64le`), count, rate, exact size, and SHA-256.
All referenced payloads are checked, including finite sample values. Project
measurements with valid raw values require both captures for their named trace. Purely synthetic
controls may omit captures; they are excluded from actual Voice/hardware counts.
There is no inferred time alignment, rate conversion, scaling, or sample fill.

Optional `render` artifacts in the envelope use the landed `validate_artifact`
and verify every declared audio/trace byte count and hash. `render_reference`
maps the stored `artifact_id` to scorecard `artifact.identity` and hashes the
original metadata bytes, not its canonical input identity. Unlike constructing
an `ArtifactStore`, validation creates no directories or lock files. These
render references supplement the measurement record; they do not substitute
for a diagnostic that substantiates the scalar row. No corpus-index schema
is broadened for directed cases.

All locators are portable relative paths, confined to their selected case;
symlinks, hardlink aliases, devices, directories in place of files, and path
traversal are refused. Capture and render validation are integrity checks, not
independent estimator reruns or proof that the producer used the named engine.
A dishonest producer can fabricate internally consistent evidence. Source,
runtime, estimator qualification, physical validation, and release approval
remain separate evidence obligations; schema validity alone establishes none.

## Freshness and completeness

`covered_inputs(registry, case, root)` returns `(fingerprint, covered_hashes)`.
The fingerprint is SHA-256 of the domain `torchsynth-case-result-v1\n` followed
by `artifacts.canonical_bytes` of:

- The full expanded case identity.
- Registry version/ID, rubric, engine, all five provenance groups (reference,
  implementation, preparation, estimator, rubric), full configuration, and all
  required row keys and limits.
- Exact current bytes of every declared provenance file and source manifest,
  plus the reader/validator implementation and schema files enumerated by
  `EVALUATOR_INPUTS`. Hash keys have distinct `project/` and `evaluator/`
  namespaces, even for isolated temporary control roots.

Dirty covered files participate immediately. Empty provenance lists or missing,
unreadable files cannot count as current. The registry policy is hashed as
content, not its generated views; evidence, views, and holdout locations are
forbidden provenance inputs. The result's identity/configuration declarations
must also equal the current policy, preventing stale metadata masquerading
under a fresh digest. A changed covered input produces STALE for a structurally
valid attempt. Missing provenance produces NO VERDICT. Corrupt allocation
metadata refuses generation before any result access.

Required rows are matched by `(trace, property, unit, estimator name/version)`
under the fixed rubric. Duplicate/conflicting rows, extra rows, wrong cases or
partitions, and omitted keys never overwrite or remove obligations. The board
retains `expected_rows`, `present_rows`, `missing_rows`, and a completeness flag.
An omitted required row forces NO VERDICT on a current attempt. Each partition
retains both allocated and inspected denominators, including NOT RUN/STALE cases.

Deterministic precedence is: malformed result/identity or unavailable provenance
→ NO VERDICT; valid attempt with changed covered inputs → STALE; incomplete row
inventory or unusable evidence → NO VERDICT; otherwise any NO VERDICT/MISSING
EVIDENCE row → NO VERDICT, else any FAIL → FAIL, else PASS. Every raw row verdict
count is retained independently. In particular FAIL plus a refusal remains
visible as one FAIL row even though the case cannot support a complete verdict.

`submitted_rows` and `row_counts` retain structurally valid producer assertions,
including stale/refused ones. Only fully verified **current** evidence appears
in `rows` and the Markdown measurement table. Stale or corrupt evidence cannot
contribute accepted numeric observations or measured-case counts. Refusals
remain null. Properties and native units are never averaged. Case outcomes and
row counts are evidence accounting; no product acceptance or quality scalar
is computed from them, including when zero failures are present.

## Holdout boundary and deterministic commands

```sh
python3 -S tools/render_scorecard.py
python3 -S tools/render_scorecard.py --check
python3 -S tests/test_case_registry.py --controls
PYTHONPATH=src python3 -S -m unittest discover -s tests -p test_case_registry.py -q
uv run --no-project --with jsonschema python tests/test_case_registry.py --schemas
```

The ordinary generation/check commands inspect development only. They do not
stat holdout attempt directories, read results, resolve artifacts, or infer
holdout outcomes. Public allocation metadata is allowed; sealed cases appear
with `access: sealed`, `outcome: null`, and unknown row availability. Assigning
NOT RUN to unopened holdout would itself invent knowledge of those results.
The Python API enforces the same refusal before any result/artifact read.

An authorized operator can explicitly select `--partition holdout
--allow-holdout 'reason/reference to authorization'`. This is not authorization
to unseal the present project holdout. That command prints an audible stderr
notice, records the reason and logical argument vector in the generated audit,
and uses separate `scorecard-holdout.json` / `SCORECARD-HOLDOUT.md` filenames.
Evidence producers must likewise record their explicit holdout access reason.
Development and holdout are never mixed in a measurement report or summed into
a measurement outcome. No real holdout command was used to build this board.

Output contains no clock, absolute host path, random identifier, or Git HEAD
self-reference. The logical generation command is the same for generation and
`--check`; the latter compares exact Markdown/JSON bytes, exits nonzero on
differences or missing views, and writes nothing. `--root`, `--registry`, and
`--output` support temporary fixtures. Outputs cannot target the evidence tree.

## Verification scope

`tests/test_case_registry.py` measures explicit synthetic sequences with the
landed `compare_paired`: equal sequences, a one-sample-delayed candidate, and a
missing input. These prove accounting and refusal behavior, not TorchSynth
sound fidelity. The tests also mutate result/record bytes, required rows,
identities, hashes, numeric states, source/configuration, and partition access;
they exercise the real render-artifact validator and exact metadata hashes.

`--controls` runs in its own process with a Python audit write fence around
production `evidence/` and `sim/reference/`, while every synthetic fixture and
result lives in a temporary root. It compares production file hashes and
directory inventories before/after and never reads sealed holdout contents.
The renderer itself has no evidence writes. Normal stdlib discovery needs no
Torch, NumPy, PDK tools, or JSON Schema package. The opt-in independent Draft
2020-12 run checks structural API/schema parity; cross-document hashes,
inventory completeness, freshness, capture bytes, and integer-token strictness
remain Python semantic checks, as with the landed scorecard contract.
