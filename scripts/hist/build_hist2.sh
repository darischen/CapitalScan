#!/usr/bin/env bash
# Resume the extended-history build. calendar, market and bars are DONE:
#   trading_days 7,293 rows from 1999-01-04
#   market_days  7,206 rows from 1998-01-13
#   bars     7,230,415 rows from 1998-09-30   (3160s)
#
# Stopped at `earnings`, which needs --historical and/or --forward N. That
# was a flag error in the first script, not a data problem.
#
# Rollback remains one command: dropdb capitalscan_hist

set -uo pipefail

REPO="C:/Users/daris/Desktop/School/CapitalScan"
cd "$REPO" || exit 1

export DATABASE_URL_RESEARCH="postgresql://capscan:capscan@localhost:5432/capitalscan_hist"
export CAPSCAN_ALEMBIC_URL="$DATABASE_URL_RESEARCH"
export CAPSCAN_SPLITS='{"ingest_start":"1999-01-01","event_start":"2000-01-01"}'

case "$DATABASE_URL_RESEARCH" in
  *capitalscan_hist*) ;;
  *) echo "REFUSING: not pointed at capitalscan_hist"; exit 2 ;;
esac
unset DATABASE_URL_SERVING

step() {
  local name="$1"; shift
  local t0=$SECONDS
  echo "=== [$(date +%H:%M:%S)] START $name ==="
  "$@" 2>&1 | tail -20
  local code=${PIPESTATUS[0]}
  echo "=== [$(date +%H:%M:%S)] END $name exit=$code elapsed=$((SECONDS - t0))s ==="
  [ "$code" -ne 0 ] && { echo "!!! $name FAILED, stopping"; exit "$code"; }
  return 0
}

# `days_to_earnings` is mean-imputed with a missingness indicator, so thin
# pre-2010 coverage degrades the feature rather than breaking the build.
# Reported, not fatal, for that reason.
echo "=== [$(date +%H:%M:%S)] START earnings (non-fatal) ==="
uv run cscan earnings --historical 2>&1 | tail -8
echo "=== [$(date +%H:%M:%S)] END earnings exit=$? ==="

# 10200 calendar days ~= 27.9 years, matching the bars window.
step indicators uv run cscan indicators --workers 8 --lookback 10200
step events uv run cscan events --lookback 10200

echo "=== BUILD HIST DONE ==="
