#!/usr/bin/env bash
# Bounded-retry `apt-get update && apt-get install -y PKG...` for CI install
# steps (issue #445). A mirror/runner stall once hung this step to its
# 10-minute cap (runs 41255462869, 42762998382); the install normally takes
# ~15 s, so a short per-attempt timeout plus a retry beats waiting out a hang.
#
# Budget: 3 attempts x 150 s + 2 x 10 s sleeps = 470 s, inside the callers'
# 10-minute `timeout-minutes` step cap, which stays as the outer bound.
# After the last failed attempt this exits non-zero: the step fails loudly,
# never a silent pass.
set -uo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: ci_apt_install.sh PACKAGE..." >&2
  exit 2
fi

attempts="${CI_APT_ATTEMPTS:-3}"
per_attempt="${CI_APT_ATTEMPT_TIMEOUT_S:-150}"
pause="${CI_APT_RETRY_SLEEP_S:-10}"
apt="${CI_APT_CMD:-sudo apt-get}"

for ((n = 1; n <= attempts; n++)); do
  if timeout "$per_attempt" bash -c '$0 update && $0 install -y "$@"' "$apt" "$@"; then
    exit 0
  fi
  echo "apt install $* failed or timed out (attempt $n/$attempts)" >&2
  if [ "$n" -lt "$attempts" ]; then
    sleep "$pause"
  fi
done
echo "apt install $* failed after $attempts attempts" >&2
exit 1
