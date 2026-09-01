#!/usr/bin/env bash
# One wrapper for the three scheduled research jobs on Linux.
#
#   scripts/run_job.sh nightly
#   scripts/run_job.sh weekly
#   scripts/run_job.sh monthly
#
# The Windows equivalent is scripts/run_job.ps1 -- the two must stay in
# step. Nothing machine-specific is written in: the repo root is derived
# from this script's location, the interpreter and CLI come from the repo's
# .venv, and the config-hash guard reads serving_config, not a literal.
#
# The guard refuses unless the resolved config hash matches what
# serving_config says is live (ADR 115), so an ablation arm left in
# core/config.py or a stray scripts/config.toml from a killed sweep cannot
# make the job write the wrong generation. A stray config.toml is cleared
# when no sweep is running; a real core/config.py change stops the job.
#
# Two guards run before the job (ADR 160):
#   1. flock -- a second trigger while a run is in flight exits 0 rather
#      than starting a concurrent writer. systemd fires these on both a
#      calendar slot and OnBootSec, so overlap is routine, not exceptional.
#   2. cscan resume-check -- skips (exit 0) when this period's run already
#      finished. Persistent=true re-fires a missed run and OnBootSec= a
#      crashed one; neither knows the chain has since completed.
set -uo pipefail

JOB="${1:-}"
case "$JOB" in
  nightly | weekly | monthly) ;;
  *) echo "usage: $0 <nightly|weekly|monthly>" >&2; exit 2 ;;
esac

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1

PY="$REPO/.venv/bin/python"
CSCAN="$REPO/.venv/bin/cscan"
[ -x "$PY" ] || { echo "no venv at $PY -- run 'uv sync' in $REPO" >&2; exit 3; }

LOGDIR="reports/$JOB"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/${JOB}_$(date +%Y_%m_%d).log"

# --- single-instance lock (before touching the log) ------------------
# Non-blocking: a losing invocation must not truncate the winner's log or
# start a second writer. The message goes to stderr because the tee to the
# day's log is not set up yet.
exec 9>"$LOGDIR/${JOB}.lock"
if ! flock -n 9; then
  echo "[$JOB] $(date '+%Y-%m-%d %H:%M:%S') another run holds the lock; exiting 0" >&2
  exit 0
fi

: > "$LOG"
# Everything from here to both the terminal and the day's log.
exec > >(tee -a "$LOG") 2>&1

echo "=== $JOB start $(date '+%Y-%m-%d %H:%M:%S') ==="

# --- resume check ---------------------------------------------------
# exit 3 -> this period's run is already status='ok', nothing to do.
# exit 0 -> run. Any other exit is an error resolving the decision and is
# treated as "run": a failure here must never block the job.
"$CSCAN" resume-check "$JOB"
rc=$?
if [ "$rc" -eq 3 ]; then
  echo "=== $JOB skip $(date +%H:%M:%S): resume-check reports this period already done ==="
  exit 0
fi

# --- config-hash guard ------------------------------------------------
resolved="$("$PY" -c 'from capitalscan.jobs.config import config_hash, resolve_config; print(config_hash(resolve_config()))' | tr -d '[:space:]')"

# serving_config is the authority; fall back to the resolved hash when
# serving is unreachable so a sleeping Pi is a no-op, not a false refusal.
expected="$("$PY" -c 'from capitalscan.jobs import sync as s; from sqlalchemy import text; c=s.serving_engine().connect(); print(c.execute(text("SELECT config_hash FROM serving_config")).scalar_one())' 2>/dev/null | tr -d '[:space:]' || true)"
if [ -z "$expected" ]; then
  echo "could not read serving_config; skipping the config-hash guard"
  expected="$resolved"
fi

if [ "$resolved" != "$expected" ]; then
  sweeping=$(pgrep -fc 'exit_sweep' || true)
  if [ "${sweeping:-0}" -gt 0 ]; then
    echo "config.toml present and a sweep is running ($sweeping); leaving it alone"
  elif [ -f "$REPO/config.toml" ]; then
    echo "found a stray config.toml (sweep arm left behind); removing it and re-resolving"
    rm -f "$REPO/config.toml"
    resolved="$("$PY" -c 'from capitalscan.jobs.config import config_hash, resolve_config; print(config_hash(resolve_config()))' | tr -d '[:space:]')"
  fi
fi

if [ "$resolved" != "$expected" ]; then
  echo "REFUSING: config resolves to $resolved, not the serving generation $expected, and no config.toml explains it. An ablation arm is probably set in core/config.py."
  exit 1
fi
echo "config ok: $resolved"

# --- run ------------------------------------------------------------
"$CSCAN" "$JOB"
code=$?
if [ "$code" -ne 0 ]; then
  echo "=== $JOB FAILED $(date +%H:%M:%S) exit=$code ==="
  echo "    per-step output is above in $LOG; the failing step is the last one"
  echo "    to print. systemd retries per the unit's Restart=/StartLimit policy."
else
  echo "=== $JOB end $(date +%H:%M:%S) exit=$code ==="
fi
exit $code
