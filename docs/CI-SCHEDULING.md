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

## Evidence record

Merging the implementation did not satisfy the live-verification and
measurement criteria.  Both are recorded below, from read-only `gh api` and
`git log` on 2026-11-26.  What was not seen is labelled **not observed** or
**not measured**; nothing is inferred from a green dispatcher run.

### What the forge still holds (2026-11-26)

`gh api --paginate 'repos/2AMLogic/gf180-torchsynth/actions/runs?per_page=100'`
returned 8,884 workflow-run records at 2026-11-26T07:18Z.  None was created
between 2026-10-01T19:43:45Z and 2026-10-22T20:03:34Z.  The cause was not
determined from this host (#566).  For this record that means:

- The rollout day and the planned measurement window (2026-10-14 to 2026-10-20)
  have no run records, so that window cannot be measured.
- The six runs in the first table below return HTTP 404
  (`gh api repos/2AMLogic/gf180-torchsynth/actions/runs/<id>`).  The table is
  kept as written on 2026-10-14 and cannot be checked again.  `check-suites`
  for the merge commits of #293 (`1d7aba1`) and #370 (`40ebff6`) is empty too.
- Run logs return HTTP 410 once they are about 30 days old.  The 19 dispatcher
  runs of 2026-10-22 to 2026-10-27 have none; all 89 later ones do.
- SigNoz `ci.run` / `ci.job` is not reachable from this host
  (`loom-daemon ci-telemetry status`: never polled).

### Live verification after merge

Observed 2026-10-14 with `gh run list` / `gh run view` (read-only). Two real
`schedule` dispatcher runs, each followed by a `workflow_dispatch` of both
suites whose own `headSha` equals the selected SHA:

| Dispatcher run | Main SHA | CI run (jobs) | TB sim run (jobs) |
|---|---|---|---|
| [38479980393](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38479980393) (2026-10-13 22:46Z) | `ea5c9b1` | [38479997870](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38479997870): contract 3.11/3.12/3.13 success | [38480012255](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38480012255): oneshot-whole-voice, oneshot-tail-chain, rtl-module-qualification, sim-lanes-engines, sim-lanes-vco, sim-selftest 3.11/3.12/3.13 all success |
| [38513918607](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38513918607) (2026-10-14 04:55Z) | `bb1efd2` | [38513933083](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38513933083): contract 3.11/3.12/3.13 success | [38513944067](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/38513944067): same 8 jobs, all success |

Schedule slots, observed 2026-10-14T11:21Z (`gh run list --workflow
ci-main-schedule.yml` and `gh api .../actions/workflows`, read-only).  The
cron is `17 */3 * * *`.  The forge reports the workflow `created_at`
2026-10-13T16:23:29Z, even though the file merged on 2026-10-07 (`9d2aa7a`).
No `CI main schedule` run of any event exists before 2026-10-13T22:46Z.
Between registration and the observation time there were six eligible slots:
10-13 18:17Z, 21:17Z, and 10-14 00:17Z, 03:17Z, 06:17Z, 09:17Z.  Only two
dispatcher runs exist.  Taking each run as the most recent preceding slot, the
21:17Z run started 22:46Z and the 03:17Z run started 04:55Z, about 89 and 98
minutes late.  The 18:17Z, 00:17Z, 06:17Z and 09:17Z slots had no dispatcher
run.  GitHub does not guarantee that scheduled workflows start on time or start
at all.

Everything after this paragraph was observed 2026-11-26, from dispatcher logs
(`gh api repos/2AMLogic/gf180-torchsynth/actions/runs/<id>/logs`), run records
and job records.  Log lines are quoted because logs expire.

**Scheduled dispatch, surviving record.**  Dispatcher run
[45310847048](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/45310847048)
(`schedule`, 2026-11-24 02:13:59Z) selected `a712d3e` and dispatched both
suites, each reporting the SHA it executed:

| Main SHA | CI run (jobs) | TB sim run (jobs) |
|---|---|---|
| `a712d3e` | [45310882381](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/45310882381), 02:14:16Z to 02:45:52Z: contract 3.11/3.12/3.13 success | [45310898349](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/45310898349), 02:14:24Z to 02:51:14Z: oneshot-whole-voice, oneshot-tail-chain, rtl-module-qualification, sim-lanes-engines, sim-lanes-vco, sim-selftest 3.11/3.12/3.13 all success |

Each suite run's own `head_sha` is `a712d3e`, the selected SHA, which was the
main tip from 2026-11-23T19:41:10Z to 2026-11-24T17:36:27Z.  Both one-shot
producers ran, and `rtl-module-qualification` started at 02:48:15Z, two seconds
after `oneshot-whole-voice` finished.  All 121 `CI` and `TB sim` runs on `main`
since 2026-10-22T20:03:34Z are `workflow_dispatch` runs by `github-actions[bot]`,
the dispatcher's identity.  None was a manual dispatch.

**Unchanged-tip no-op: observed.**  At the same tip, dispatcher run
[45406700843](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/45406700843)
(`schedule`, 2026-11-24 13:45:18Z) logged (SHAs shortened):

```
selected main @ a712d3ed6412…
ci.yml: reuse @ a712d3ed6412… (exact-SHA successful run) evidence/run: https://github.com/2AMLogic/gf180-torchsynth/actions/runs/45310882381 sha a712d3ed6412…
tb-sim.yml: reuse @ a712d3ed6412… (exact-SHA successful run) evidence/run: https://github.com/2AMLogic/gf180-torchsynth/actions/runs/45310898349 sha a712d3ed6412…
```

It names the two runs in the table above, and no other `CI` or `TB sim` run
exists at `a712d3e`.  Of the 88 dispatcher runs that logged a decision
(2026-10-27 to 2026-11-26), 38 are no-ops for both suites, 49 dispatched both,
and one
([40797512946](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/40797512946),
2026-10-29T00:58:38Z) reused `CI` and dispatched `TB sim` again because the
newest `TB sim` run at that SHA had failed
([40761851690](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/40761851690),
2026-10-28T20:16:52Z: `sim-selftest` 3.11/3.12/3.13 failed).  The replacement,
[40797534730](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/40797534730),
failed the same three jobs, so an older success did not hide a newer failure.
Each of the 77 `reuse` decisions names an earlier, successful run of the same
workflow on `main` at the same SHA.  No logged run chose `active`, reported an
executed SHA different from the selected one, or re-selected after
`main advanced`.  The nine dispatcher runs on `396fb53` (2026-10-17 to
2026-10-19) are **not verified**: their records are gone.

**Main advancement: one case observed, one not observed.**

- *A started suite run outliving a newer tip: observed 14 times*, at 7 tips
  with a `CI` and a `TB sim` run each.  All 14 ended `success`; none was
  cancelled.  Example, 2026-11-01: dispatcher run
  [41311280457](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/41311280457)
  (13:26:42Z) dispatched at `ed9f9cc`.  `CI`
  [41311293860](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/41311293860)
  ran 13:26:52Z to 13:57:34Z and `TB sim`
  [41311304396](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/41311304396)
  ran 13:27:01Z to 13:57:17Z, both success.  PR #433 merged as `6301092` at
  13:41:55Z, about 15 minutes into both runs, and both ran to completion.  The
  next dispatcher run,
  [41343037914](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/41343037914)
  (19:41:55Z), selected `6301092` and dispatched both suites
  ([41343053234](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/41343053234),
  [41343065225](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/41343065225),
  both success).
- *A newer main run waiting behind a started one: not observed.*  Of the 1,882
  runs on `main` since 2026-10-22T20:03:34Z, none was cancelled, and none was
  created while an earlier run of the same workflow on `main` was still in
  progress.  The waiting and replacement half of rule 2 has not been exercised
  on `main`; only Dependabot's runs overlapped each other.  The four cancelled
  runs in that period are pull-request runs (2026-11-19).

### Seven-day measurement

Rollout: PR #293 merged as `1d7aba1` at 2026-10-13T16:23:27Z, and the forge
registered the dispatcher at 16:23:29Z.  That is the effective rollout start,
not the 2026-10-07 merge of the file (`9d2aa7a`).  The planned window, the
seven complete UTC days 2026-10-14 to 2026-10-20, is **not measured**: its run
records are gone.  Two later seven-day windows are measured with one method:

- **A**, 2026-10-23 to 2026-10-29: the first seven complete days with records.
- **B**, 2026-11-19 to 2026-11-25: the latest seven complete days.

Method.  The source is the GitHub REST API, not SigNoz.  A window is half-open
on `created_at`, in UTC.  A job-minute is `completed_at − started_at`, summed
over every job of every attempt of every run created in the window
(`repos/2AMLogic/gf180-torchsynth/actions/runs/<id>/jobs?filter=all`), not
rounded up per job.  Main job-minutes cover the 13 small suites, the dispatched
`CI` and `TB sim` runs, the dispatcher and Dependabot.  Merges are first-parent
commits on `main` (`git log origin/main --first-parent`), which equal the
distinct pushed SHAs.  Columns are rounded; totals are computed first.

| Per day unless stated | Baseline (issue: SigNoz, 7 days to 2026-10-06) | Before rollout, same method (2026-09-28 to 09-30, 3 days) | A (2026-10-23 to 10-29) | B (2026-11-19 to 11-25) |
|---|---|---|---|---|
| Merges to `main` | 16 | 41 (13.7) | 18 (2.6) | 23 (3.3) |
| `CI` runs on `main` | 16, on push | 41, on push | 13, dispatched | 9, dispatched |
| `CI` job-min | 609 | 407 | 147 | 104 |
| `TB sim` runs on `main` | 16, on push | 41, on push | 14, dispatched | 9, dispatched |
| `TB sim` job-min | 913 | 402 | 157 | 105 |
| 13 small suites, job-min | about 210 | 175 | 46 | 60 |
| Dispatcher and Dependabot, job-min | not stated | 0 | 1.7 | 1.3 |
| **Main job-min** | **1,721** | **984** | **351** | **270** |
| PR job-min | about 830 | 649 | 598 | 666 |
| Job-min per `CI` run, per `TB sim` run | 38, 57 | 30, 29 | 79, 78 | 81, 82 |
| Dispatcher runs, of 8 cron slots a day | not stated | not scheduled | 27 of 56 | 19 of 56 |

The before-rollout column includes 2026-09-29 and 09-30, the baseline window's
days that still have records, plus 09-28 for sample size.

Outcomes on `main`.  A: 291 runs, 288 success, 3 failure, 0 cancelled.  The
failures are the two `TB sim` runs at `9db7231` above and dispatcher run
[40931255953](https://github.com/2AMLogic/gf180-torchsynth/actions/runs/40931255953)
(2026-10-29T18:29:14Z), whose job failed after one second; its log has expired,
so the cause is not recorded.  B: 339 runs, all success.  Pull-request runs:
A, 352 success and 8 failure; B, 415 success, 1 failure and 4 cancelled.

Comparison with the baseline.

- Main job-min/day is 80% (A) and 84% (B) below 1,721, but most of that is not
  the policy: merges fell from 16 to 2.6 and 3.3 a day.  The before-rollout
  column also shows that this method and the baseline are not the same
  measure: 72 against 108 job-min per merge.
- Like for like: at the old push trigger, each merge would have run both
  suites once at the measured cost.  For A that is 18 × (79 + 78) job-min in
  the window, about 451 job-min/day with the small suites; 351 was measured, a
  saving of 22%.  For B the same arithmetic gives about 594 against 270
  measured, a saving of about 55%.
- Main job-min per merge rose from 72 before the rollout to 136 (A) and 82 (B),
  because each suite now runs on a schedule (13 and 14 times in A, 9 in B)
  instead of once per merge.
- The forecast (about 900 job-min/day, −48%) assumed about 95 job-min for one
  `CI` plus one `TB sim` run.  A pair now costs 157 (A) to 162 (B): `CI` went
  from about 30 to about 80 job-min a run, and `TB sim` from about 29 with 5
  jobs to about 80 with 8 (the two one-shot producers were added and
  `sim-lanes` was split in two).  Eight runs a day would cost about 1,260 to
  1,300 job-min for the two suites alone.  The measured 303 (A) and 209 (B) is
  lower because the cron produced 27 and 19 dispatcher runs in 56 slots, 5 to
  165 minutes late (median 92) in A and 29 to 158 minutes (median 120) in B,
  and because unchanged tips were skipped.
- PR job-min/day is 598 (A) and 666 (B) against about 830; the policy does not
  change pull-request runs.
- Rounded up per job instead, main is 404 (A), 331 (B) and 1,268 (before the
  rollout) job-min/day.

The forecast is therefore neither confirmed nor refuted at its own
assumptions, and the drop against the baseline is mostly a change in workload.
Changing the interval is a separate decision (#565).
