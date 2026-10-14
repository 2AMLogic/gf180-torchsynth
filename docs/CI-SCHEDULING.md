# CI scheduling on main

Issue #284.  Policy source: 2AMLogic/2am `docs/ci.md`, "Default-branch runs".

## Policy

1. A started run on main is never cancelled.
2. At most one main run waits per workflow; a newer one replaces the waiting one.
3. The heavy suites (`CI`, `TB sim`) do not run on every merge.  They run on the
   newest main on a schedule, unless that exact commit already has a
   successful run of that suite.

Every workflow that runs on push to main (and `CI`, `TB sim` and the dispatcher)
carries the top-level stanza

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

so only pull-request events cancel an in-progress run.  `capabilities.yml`
formerly serialized its `refresh` job with a job-level group; that is replaced
by this workflow-level policy (the job keeps its main/repository guard,
`contents: write`, and plain fast-forward push).

## What runs when

| Workflow | PR | Push to main | Schedule | Manual |
|---|---|---|---|---|
| `ci.yml`, `tb-sim.yml` | every change | no | via dispatcher | yes (always executes) |
| `ci-main-schedule.yml` | no | no | `17 */3 * * *` UTC | yes |
| other 12 suites | every change | every merge | no | unchanged |

The three-hour cadence is a scheduling target, not a guarantee that GitHub
starts or finishes a run within three hours.  Whether to shorten it is decided
by the seven-day measurement below.

## Dispatcher and selector

`.github/workflows/ci-main-schedule.yml` runs `tools/dispatch_main_ci.py` with
`contents: read` and `actions: write` (the built-in `GITHUB_TOKEN`; no stored
token).  PR test jobs gain no write permission.  For the current main tip, per
suite, it chooses:

- **reuse**: the newest non-PR run of the same workflow file, same repository,
  branch `main`, exact same `head_sha` completed with `success`, and its jobs
  (every required job and matrix leg, including for TB sim both one-shot
  producers and `rtl-module-qualification`) all succeeded.  The no-op reports
  the evidence run URL and SHA.  It creates no substitute pass or artifact.
- **active**: a queued/in-progress run for that workflow and SHA exists; no
  redundant dispatch.
- **dispatch**: anything else, including a newest run that failed, was
  cancelled, skipped or timed out (an older green attempt never hides it).

PR runs, other workflows, other SHAs and dispatcher runs are never evidence.
API, authentication or parse failures (including a malformed run list while
looking for a dispatched run, and a run whose `status` is missing or not a
recognised GitHub status, which is never treated as an active execution) fail the dispatcher step; they are never treated
as a cache hit or as "run not yet visible".  The tip is re-read after selection,
again immediately before each suite's decision is acted on, and again
immediately before each dispatch; if main advanced, every suite not yet acted on
is re-selected at the new tip (bounded to three re-selections, then the step
fails).  After dispatch the selector
polls for the resulting run and reports that run's own `head_sha` as what
actually executed; a dispatch executes whatever main is when it starts.

A green `CI main schedule` run only means selection ran.  It is never a CI or
TB sim result; the dispatched suite runs are.  A manually dispatched suite
always executes again.  A `cancelled` main run is no verdict, not a failure;
do not re-run it to complete the record.

Trade-off: a break that only appears after merge is found up to one window
later, and Loom's per-commit main-health gate reads commits between batches as
"no verdict".

## Tests

- `python3 -m unittest discover -s tests -p 'test_ci_scheduling.py' -v`:
  selector fixtures (no history, exact-SHA success, mismatched SHA / workflow /
  branch / PR / repo, every non-success conclusion, skipped required job,
  active run, later failed attempt, pagination, API failure, advancing main
  during selection and before/while dispatching, malformed run-list responses) and
  workflow-inventory checks with negative fixtures.
- `python3 -m unittest discover -s tests -p 'test_ci_lane_wiring.py' -v`
  continues to guard the RTL lanes, budgets and failure propagation of
  `tb-sim.yml`; the dispatcher never replaces those jobs.

## Pending evidence (not yet recorded)

Merging the implementation does not satisfy the following; issue #284 stays
open until they are recorded here with dates, commands and run URLs.

### Live verification after merge: PARTIAL

Recorded 2026-10-14 from `gh run list` / `gh run view` / `gh run view --log`
on `2AMLogic/gf180-torchsynth`.

**Recorded: real scheduled dispatch and both resulting suite runs.** Two
`schedule`-event runs of `ci-main-schedule.yml` (`select-and-dispatch` job,
success) each dispatched `ci.yml` and `tb-sim.yml` on the selected main SHA; the
dispatched runs report the same executed SHA as the selection.

| Dispatcher run | Selected / executed SHA | `ci.yml` run (jobs) | `tb-sim.yml` run (jobs) |
|---|---|---|---|
| [38479980393](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38479980393), 2026-10-13T22:46Z | `ea5c9b1006080ee0183169bffdf1420d1292b75b` | [38479997870](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38479997870): `contract` 3.11/3.12/3.13, all success | [38480012255](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38480012255): `oneshot-whole-voice`, `oneshot-tail-chain`, `rtl-module-qualification`, `sim-lanes-engines`, `sim-lanes-vco`, `sim-selftest` 3.11/3.12/3.13, 8 jobs, all success |
| [38513918607](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38513918607), 2026-10-14T04:55Z | `bb1efd2376c7864a5dd5259a3925aff943dc0b02` | [38513933083](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38513933083): 3 jobs, all success | [38513944067](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38513944067): 8 jobs, all success (`sim-lanes-engines`, `sim-lanes-vco`, `sim-selftest` x3 confirmed by name) |

Both selections logged `no execution for this SHA`, so each dispatched both
suites. The dispatched suites ran as `workflow_dispatch` events, not `push`.
Observed cadence: the 18:17 and 00:17 UTC cron slots have no run in
`gh run list` (GitHub may delay or drop scheduled runs), so the three-hour
cadence is a target, not an observed guarantee.

**Still PENDING (not observed):**

- A later unchanged-tip no-op linked to prior successful evidence. Every
  scheduled run so far found a new tip, so none has exercised the reuse path
  against the real API.
- A main-advancement case showing a started main run was not cancelled. In
  both runs above, main did not advance while the suites were executing, so
  this has not been demonstrated.

### Seven-day measurement: PENDING

Record rollout SHA, query/window definition, main job-min/day including
scheduled/manual suite execution and dispatcher overhead, PR job-min/day, merge
counts, run outcomes, and comparison with the baseline (16 merges/day, 1,721
main job-min/day, PRs about 830/day; 7 days to 2026-10-06).  The roughly 48%
reduction at a three-hour cadence is a forecast, not a result.
