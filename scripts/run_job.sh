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
: > "$LOG"
# Everything from here to both the terminal and the day's log.
exec > >(tee -a "$LOG") 2>&1

echo "=== $JOB start $(date '+%Y-%m-%d %H:%M:%S') ==="

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
echo "=== $JOB end $(date +%H:%M:%S) exit=$code ==="
exit $code
