# Capability views and automatic refresh

`spec/capabilities-v1.json` is the only editable capability graph. It declares
claim scope, dependencies, covered inputs/references, registered or planned
checks, required controls, exclusions, and evidence pointers. The Loom issue
DAG schedules implementation; it is not an input to evidence evaluation.

The existing compiler resolves these declarations through strict evidence
records, exact stored bytes, current source/spec/tool hashes, registered check
identity, prerequisite evidence and controls. Its detailed status precedence
and producer-attestation trust boundary are in [CAPABILITIES.md](CAPABILITIES.md).
No arbitrary evidence command is run. No expensive measurements or holdout
access are introduced by this refresh mechanism.

## Local commands

```sh
python3 -S tools/compile_capabilities.py
python3 -S tools/compile_capabilities.py --check
python3 -S tools/compile_capabilities.py --strict
python3 -S -m unittest discover -s tests -p 'test_capabilit*.py' -v
```

Generation derives three views from one evaluation:

- `docs/CAPABILITIES.md`: full node table, dependency graph, reasons and exclusions.
- `docs/capabilities.json`: deterministic machine-readable projection with all
  node declarations, effective/local states, reasons, evidence health and all
  seven state counts (including zeros). `kind=generated-capability-view` marks
  it as a view, not a qualification record or another canonical graph.
- The single `CAPABILITIES:BEGIN` / `CAPABILITIES:END` region in the root README:
  state counts and the same dependency graph. Bytes outside the region are
  preserved, including line endings. Missing/duplicate/reversed/inline markers
  are errors; the compiler never guesses where to overwrite handwritten prose.

All views are computed before any output is written. Unchanged files are not
rewritten. View paths must be distinct safe relative paths inside `--root` and
must not overwrite the graph. Generated views contain neither wall-clock time
nor the repository HEAD hash, avoiding self-invalidating commits. Generated
views are not covered inputs of the synthetic compiler qualification check.

`--check` is read-only and fails on invalid graph/view disagreement. It does not
certify evidence health: a correctly displayed FAIL or STALE may agree.
`--strict` is also read-only, checks the same three views, then additionally
fails on unhealthy declared evidence, including missing/malformed records,
failed controls, stale covered inputs and attempted-but-blocked claims.
Planned nodes with no evidence are explicitly allowed, never PASS. Passing a
strict check means honest evidence accounting, not a product/hardware verdict.

Bounded runtime, scalar, estimator or trace measurements elsewhere in the
repository are not denied by an unrun capability node. They have not been
silently adapted into the graph's broader claims. A future registration needs
reviewed scope-specific validation and a current qualification record; closed
issues, files, test results or legacy smoke logs alone cannot stamp it.
There is no aggregate progress percentage across incompatible claim classes.
The [scorecard](SCORECARD.md) independently owns case/property coverage.

## GitHub Actions safety

`.github/workflows/capabilities.yml` runs read-only graph/view tests, agreement
and strict health checks on every pull request. It never checks out PR code
with a write token. Pushes to main and manual runs on main regenerate views
from a fresh checkout of main under a serialized, non-cancelling refresh job.
Only that job has `contents: write`; no personal access token is required.

The refresh validates generation and tests before staging exactly the three
generated files. It publishes changed states even when strict evidence health
fails, then explicitly fails the job so a red claim is visible rather than
silently hidden behind a stale green view. Malformed graphs or broken markers
stop publication. Unchanged views make no commit. A normal fast-forward push
is used: branch protection or a concurrent main update causes a visible failure,
never a force push or automatic policy bypass. The next main event/manual run
can regenerate from the newer head.

Generated-only pushes are excluded by path filters. Independently, a push made
with the repository `GITHUB_TOKEN` does not recursively trigger push workflows
([GitHub trigger documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)).
No skip-CI tag, commit-message-controlled bypass, or issue/label event is used.
Distinct PR checks are not cancelled; only refresh publication is serialized.
The write path must first run after merge; PR CI cannot demonstrate permission
to push to protected main and this implementation does not claim that it has.

The sibling Parasynth DAG inspired generated README refresh only. Its IDs,
statuses, tolerances, tag/existing-file attestations and command execution
mechanisms were not imported. Existing TorchSynth graph/evidence v1 schemas
and fail-closed resolver semantics remain authoritative.
