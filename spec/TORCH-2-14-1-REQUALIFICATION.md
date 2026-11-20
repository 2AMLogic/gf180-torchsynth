# PyTorch 2.14.0 -> 2.14.1 requalification (issue 394, follows PR 393)

Status: **lock regenerated; no qualification run executed.** Every run-dependent
item below is `NOT_RUN`. This record produces no numerical PASS and does not
make PR 393 merge-ready.

## Lock regeneration (executed)

- Base: `main` @ `c0d24b3`, `uv.lock` sha256 `14f8730c60da411b…`, `revision = 5`.
- Tool: host `uv 0.13.1` used read-only (`uv lock --upgrade-package torch`,
  then `uv lock --check`); nothing was installed on the host.
- Result: `revision = 5` retained; the diff is the `torch` 2.14.0 -> 2.14.1
  version and its wheel entries only (21 lines). PR 393's `revision = 5 -> 3`
  header regression is not reproduced.
- New `uv.lock` sha256: `4316abb62c3c0d294c290756b955143c0cde058e81afa51de90c06a8aebb51bc`.

## Inventory of `uv.lock` dependents

| File | Role | Depends on `uv.lock` bytes? |
|---|---|---|
| `env/release-era/qualify_scalar.py` (`+ ["uv.lock"]`) | definition (provenance hash list) | yes |
| `sim/reference/scalar-execution.json` | executed evidence | yes: binds `14f8730c…` in `definition_sha256` |
| `env/release-era/repeatability-matrix.json` `current` | definition | yes, and already stale (`66ce6615…`) |
| `env/release-era/qualify_repeatability.py` | definition (lock-hash check) | yes, fails safe |
| `sim/reference/repeatability-runtime.json` | executed evidence | historical, tied to `66ce6615…` |
| `tools/qualify_preparation.py`, `tools/qualify_rtl_modules.py` | consumers | name `uv.lock`, embed no hash |
| `spec/decision-records/0006-canonical-runtime.md`, `env/release-era/README.md` | prose | no |

The release arm (`requirements.lock`, Python 3.9.13, torch 1.12.1+cpu) and its
source pin are untouched.

## Pre-existing baseline

The `current` pin `66ce6615…` was already stale on `main` before PR 393
(`CANONICAL-REFERENCE-QUALIFICATION.md`, drift item 3). It is left as is; it
is not overwritten without the recorded run it describes.

## Not run

| Step | Status | Reason |
|---|---|---|
| Scalar qualification (`qualify_scalar.sh`, regenerates `scalar-execution.json`) | `NOT_RUN` | Needs a linux/amd64 pinned-image build and two process pairs on a qualified substrate. The executing host is a shared dispatch worker (x86_64), not a recorded qualification host, and the `repo-remote` box has no SSH alias here. The committed `scalar-execution.json` still binds the old lock hash, so provenance verification against the new `uv.lock` is expected to fail until a real recapture runs. |
| Repeatability `current` arm, torch 2.14.1 | `BLOCKED` | DR-0006 pins the comparator to macOS arm64 / Apple M5. No such host is reachable from this worker; Linux is not substituted. All requested cells are `NO_VERDICT`. |
| `repeatability-matrix.json` `current` update | not changed | Updating lock hash/torch version without the run would describe an environment that was never executed. |

## Follow-up needed

A person with the Apple M5 host and a qualified linux/amd64 host runs the
scalar recapture and the `current` repeatability campaign on this lock, then
updates `scalar-execution.json` and the `current` definition from those actual
runs. No contract change has been identified, so no decision record is
triggered by this change.
