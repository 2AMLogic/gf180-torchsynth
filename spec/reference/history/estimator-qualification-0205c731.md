# Estimator qualification and refusal ledger v1

This is the versioned consumer/publication ledger for estimator validity,
floors, and refusals over the landed producer family grids. It integrates
existing reviewed evidence; it implements no estimator algorithm, tunes
nothing on fixed-point results, and derives no tolerance from holdout or
future RTL data. The source target remains
`2b0964d4c6c3d472a2a0d54d91b408caaeffca6d`.

## What this ledger is

One commit, `sim/qualification/estimators-v1.json`, binds every mandatory
estimator family to its version, configuration identity, validity predicate,
qualification grid, floors, and known limitations, and counts coverage and
floor-limited cases separately from PASS/FAIL. All refusals serialize as
`NO VERDICT` (DR-0004); absent input evidence stays the distinct
`MISSING EVIDENCE` state. The ledger is a reviewed coverage policy, not a
second implementation of any family algorithm: it calls the landed public
adapters' already-committed artifacts and recomputes their accounting from
validated raw rows.

Families (all mandatory; no family may be dropped by a checker):

| Family | Producer | Artifact | Algorithm identity |
| --- | --- | --- | --- |
| `paired` | #25 | `sim/qualification/periodic-v1.json` (paired sentinels) | `paired-metrics-v1` |
| `periodic` | #26 | `sim/qualification/periodic-v1.json` | `periodic-v2` |
| `envelope` | #27 | `sim/qualification/envelope-routes-v1.json` | `envelope-routes-qualification-v1` |
| `spectral` | #28 | `sim/qualification/spectral-noise-mix-v1.json` | `spectral-noise-mix-v1` |
| `preparation` | #86 | `sim/qualification/preparation-v1.json` | `preparation-qualification` |

Floors are kept as each producer recorded them: envelope floors are
per-domain empirical statistics (maximum/mean error, minimum tested stage,
refused and valid trial counts); spectral floors compare measured maxima
against independently preregistered bounds as distinct fields; periodic
range cells carry guard floors and cap failures per cell. The ledger never
rewrites a producer rubric under an umbrella rubric and never widens a floor
to pass a comparison; floor-limited comparisons refuse.

## Validity, coverage, and refusals

`src/torchsynth_voice/estimator_qualification.py` recomputes each family's
census from validated raw evidence and refuses on any gap:

- Every scorecard row in every family validates as scorecard v1 via the
  public `scorecard.validate_row` / `validate_report` contract.
- Periodic case counts are recomputed from `measurement.status` values, not
  trusted from the artifact's own summary.
- Envelope obligations must form a duplicate-free set, every obligation must
  observe its expected verdict with `satisfied=true`, and every obligation
  must own at least one report row. Report row keys beyond the preregistered
  obligations are counted as producer census, never silently dropped.
- Spectral refusal reasons and rows are summed from the taxonomy; a mutation
  counts as exercised detection only when an assigned valid detector actually
  reports FAIL — a `detected=true` flag alone, a refusal, or an unrelated
  FAIL is not detection.
- Preparation checks are counted separately from its runtime receipts.

Coverage is reported as requested/present/qualified/refused/missing per
family and again in totals; refusals, floor-limited cases, and missing
evidence are never merged into pass/fail counts, and unlike units are never
averaged into a fidelity score.

## Shared preparation binding

`src/torchsynth_voice/preparation.py` is the sole shared preparation layer.
The ledger recomputes its SHA-256 from the actual tree and reconciles it
against every landed declaration. At ledger version 1 all three landed pins
(envelope `shared_preparation`, the preparation artifact's
`implementation_sha256`, the spectral probe) and the prose in
`spec/SIGNAL-PREPARATION.md` reproduce one digest:

`0f8e9e6ee2b31dd6b2b2f5118e81a90d08849d013da13ff04d0c3100c660d297`

The digest `52d42960f1fec157a48e9ba0bc35fb13619c0c6b3f4274fa711cd22c58a99426`
recorded by PR101's pre-merge snapshot appears in no landed artifact. This
recomputation is recorded in the ledger's `provenance` block; the historical
snapshot is retained as history, not rewritten. If a future recomputation
disagrees with any landed pin, the ledger records status `inconsistent` and
the publication gate below activates.

A preparation failure — missing, failed, stale, or malformed evidence, or a
provenance inconsistency — forces every preparation-dependent family
(`periodic`, `envelope`, `spectral`) to `NO VERDICT` in the publication view
with a concrete reason, while raw rows, verdicts, and diagnostics are
preserved untouched. `paired` and `preparation` keep their honest state.
`gate_dependent_families` implements this view; it never erases a failure or
manufactures a digest for absent data.

## Runtime evidence status

The preparation artifact's analytic `runtime_integration` field records
`PENDING`; that producer field is superseded — at this publication layer
only — by the four `actual_integrations` receipts in the same artifact, each
with `evidence_status: VALIDATED` and production status `NO_VERDICT`. That
refusal is the DR-0007-mandated outcome for a diagnostic oracle, recorded as
the final disposition in `spec/SIGNAL-PREPARATION.md`; it is not an
outstanding assertion, and it grants no normative production oracle.

## Reproduction and claim boundaries

```sh
# Stored-evidence validation (stdlib only; no NumPy required):
python3 tools/qualify_estimators.py check

# Rebuild the committed ledger from the landed artifacts:
python3 tools/qualify_estimators.py build

# Executed qualification: rerun the landed family tools from a clean
# checkout and compare recomputed case/row/obligation identity. Requires
# the declared numerical extra (uv sync --locked --extra metrics) and
# refuses without it; it never silently skips.
python3 tools/qualify_estimators.py replay
```

`check` is a three-way comparison: the recomputed census, the committed
ledger, and the reviewed inventory
`spec/reference/estimator-obligations-v1.json` must agree exactly — exact
key sets and multiplicities, not lengths or prefixes. The inventory is the
reviewed consumer policy; regenerating it is reproduction, not authority,
and any drift is an error to reconcile under a new reviewed ledger version,
with old evidence retained.

`replay` delegates grid execution to the landed producer tools exactly as
their family CI workflows do, then compares recomputed census identity. It
does not assert cross-host byte portability; periodic/preparation exact-byte
replay and envelope/spectral numeric replay remain each producer's own
declared contract, run in their own workflows. A single executed replay does
not transfer floors to configurations the grids did not exercise.

This publication does not ratify a CPU/runtime/scalar profile, force
batch-1/batched byte equality, activate unmeasured Voice patches, establish
canonical Torch noise, freeze float/fixed/hardware tolerances, implement an
independent float model, render holdout, or qualify RTL, listening, or
physical hardware. Perceptual/population benchmarking (#36/#45) and
independent float/Voice conformance (#43) consume this ledger and do not
change it. Analytic estimator qualification, analytic preparation
qualification, actual runtime consumption, and directed Voice integration
remain separately published claims; an analytic-only ledger cannot imply the
runtime integration claim beyond the receipts recorded here.
