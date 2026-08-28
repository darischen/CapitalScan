#!/usr/bin/env bash
# The Pi's poller wrapper: wait for the open, then poll until the close.
#
# The Linux counterpart of `scripts/wait_and_poll.ps1`, with two deliberate
# differences.
#
# **It writes the serving store, not research (ADR 158).** Every poller row
# is provisional -- nightly sweeps them and recomputes the authoritative
# version from bars -- so research never needed them. Removing it from the
# live write path is what frees the workstation during market hours: no
# second writer on `events`, so it can be shut down, updated, or given a
# rebuild.
#
# **`cscan poll --serving` refuses to start against a stale target.**
# Writing research, the poller read the store every other job writes, so it
# could not be behind. Writing serving it reads a *copy*, and a copy that
# missed a sync still answers `universe` and `indicators` with last week's
# data rather than failing. The guard is in `poll.assert_target_is_current`.
#
# Started by `capitalscan-poller.timer` at 00:00 local, which is why the
# wait loop below exists rather than the timer firing at 06:45 directly:
# starting at midnight means the unit is already up and its log already
# open when the session begins, so a failure to start is visible hours
# before it would matter.
set -uo pipefail

INTERVAL="${1:-300}"
OPEN="06:45"    # 15 minutes after the 06:30 PT bell (user's decision,
                # 2026-08-24). See wait_and_poll.ps1 on why this caps
                # `coverage_pct` at ~96% permanently.
CLOSE="13:00"

cd "$(dirname "$0")/../.." || exit 1

say() { echo "[$(date '+%H:%M:%S')] $*"; }

secs() { date -d "$1" +%s; }

say "poller wrapper up. open ${OPEN}, close ${CLOSE}, interval ${INTERVAL}s"

now_s=$(date +%s)
open_s=$(secs "$OPEN")
close_s=$(secs "$CLOSE")

if (( now_s > close_s )); then
  say "already past the close; nothing to do today"
  exit 0
fi

if (( now_s < open_s )); then
  wait_s=$(( open_s - now_s ))
  say "waiting $(( wait_s / 3600 ))h $(( (wait_s % 3600) / 60 ))m until ${OPEN}"
  sleep "$wait_s"
fi

say "starting poll --serving"
# `--serving` also skips the research->serving push: copying serving to
# serving is a no-op that would corrupt the watermark.
exec .venv/bin/cscan poll --interval "$INTERVAL" --serving
