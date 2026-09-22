#!/usr/bin/env bash
# Events for the isolated store, in RESTARTABLE ticker chunks.
#
# **Why chunked, and why by ticker.** A single `cscan events` pass over
# 2002-2026 ran 4h40m, wrote nothing, and gave no progress signal -- it
# accumulates and upserts once, so a failure at hour nine loses everything
# and there is no way to tell 20% from 80%. Chunking by ticker fixes all
# three: each chunk writes on completion, progress is visible in the row
# count, and a restart skips the chunks already done.
#
# **By ticker is safe; by date would not have been.** `cofire_count` is the
# one cross-ticker feature and `run_events` does not compute it --
# `cscan backtest --phase finalize` does, grouping across tickers by
# (signal_date, signal_type). cli.py says that pass "cannot live inside a
# resumable per-chunk loop", which is exactly why it runs once at the end
# here rather than per chunk.
#
# Rollback: dropdb capitalscan_hist

set -uo pipefail
cd "C:/Users/daris/Desktop/School/CapitalScan" || exit 1
SP="$(dirname "$0")"

export DATABASE_URL_RESEARCH="postgresql://capscan:capscan@localhost:5432/capitalscan_hist"
export CAPSCAN_SPLITS='{"ingest_start":"1999-01-01","event_start":"2002-01-01"}'
export CAPSCAN_UNIVERSE='{"required_criteria":["crit_above_sma200","crit_sma200_slope","crit_rel_return"]}'

case "$DATABASE_URL_RESEARCH" in
  *capitalscan_hist*) ;;
  *) echo "REFUSING: not pointed at capitalscan_hist"; exit 2 ;;
esac
unset DATABASE_URL_SERVING

PSQL="/c/Program Files/PostgreSQL/18/bin/psql"
q() { PGPASSWORD=capscan "$PSQL" -h localhost -p 5432 -U capscan -d capitalscan_hist -tAc "$1"; }

# Only tickers that actually have bars; the rest cannot produce an event.
mapfile -t ALL < <(q "SELECT DISTINCT ticker FROM bars ORDER BY ticker")
echo "tickers with bars: ${#ALL[@]}"

CHUNK=60
total=${#ALL[@]}
n_chunks=$(( (total + CHUNK - 1) / CHUNK ))
echo "chunk size $CHUNK -> $n_chunks chunks"
echo "start $(date +%H:%M:%S)"

done_file="$SP/events_chunks_done.txt"
touch "$done_file"

for ((i = 0; i < total; i += CHUNK)); do
  idx=$(( i / CHUNK + 1 ))
  if grep -qx "$idx" "$done_file"; then
    echo "  [$idx/$n_chunks] already done, skipping"
    continue
  fi
  slice=("${ALL[@]:i:CHUNK}")
  csv=$(IFS=,; echo "${slice[*]}")
  t0=$SECONDS
  out=$(uv run cscan events --lookback 10200 --tickers "$csv" 2>&1 | tail -3)
  code=$?
  rows=$(q "SELECT count(*) FROM events")
  if [ "$code" -eq 0 ]; then
    echo "$idx" >> "$done_file"
    echo "  [$idx/$n_chunks] ok in $((SECONDS - t0))s | events total now $rows"
  else
    echo "  [$idx/$n_chunks] FAILED ($((SECONDS - t0))s): $out"
  fi
done

echo "=== [$(date +%H:%M:%S)] all chunks attempted, events=$(q 'SELECT count(*) FROM events') ==="

# The one cross-ticker pass, after every chunk is in.
echo "=== [$(date +%H:%M:%S)] START finalize (cofire_count) ==="
uv run cscan backtest --phase finalize 2>&1 | tail -6
echo "=== [$(date +%H:%M:%S)] END finalize exit=$? ==="

echo "=== EVENTS CHUNKED DONE ==="
