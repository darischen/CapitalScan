#!/usr/bin/env bash
# The three label stages, on a ticker SUBSAMPLE, in the isolated store.
#
# `run_events` writes signals but not the four labels the model trains on:
#   backtest --phase compute  ->  fwd_ret_5d / fwd_ret_10d
#   path backfill             ->  the path rows peak labels read
#   path peak-labels          ->  peak_ret_5d / peak_ret_10d
#
# **Subsampled deliberately.** A full pass is 8-15 hours on 1,463 tickers.
# Both arms read the SAME subsample, so the A/B stays valid -- only the
# absolute event counts shrink, and those were never comparable to
# production anyway (crit_mcap is dropped in this store).
#
# **Sampled deterministically and spread across the alphabet**, not the
# first N: `ORDER BY ticker` would bias toward whatever sectors cluster
# early, and sector is a model feature.
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

N=400
SUBSET=$(q "SELECT string_agg(ticker, ',' ORDER BY ticker)
            FROM (SELECT ticker FROM events GROUP BY ticker
                  ORDER BY md5(ticker) LIMIT $N) s")
echo "$SUBSET" > "$SP/subset_tickers.txt"
echo "subsample: $(echo "$SUBSET" | tr ',' '\n' | wc -l) tickers"
echo "events in subsample: $(q "SELECT count(*) FROM events WHERE ticker = ANY(string_to_array('$SUBSET', ','))")"
echo "start $(date +%H:%M:%S)"

step() {
  local name="$1"; shift
  local t0=$SECONDS
  echo "=== [$(date +%H:%M:%S)] START $name ==="
  "$@" 2>&1 | tail -12
  local code=${PIPESTATUS[0]}
  echo "=== [$(date +%H:%M:%S)] END $name exit=$code elapsed=$((SECONDS - t0))s ==="
  [ "$code" -ne 0 ] && { echo "!!! $name FAILED"; return 1; }
  return 0
}

# Resumable per chunk: `_chunk_already_done` keys on (config_hash, chunk, of),
# so the chunk size must not change across restarts.
step compute uv run cscan backtest --phase compute --workers 8 --chunk-size 25 --tickers "$SUBSET" \
  || { echo "compute failed, stopping"; exit 1; }

# `path backfill` has no --tickers flag; it walks the events already
# filled by compute, which is exactly the subsample.
step pathbackfill uv run cscan path backfill --workers 8
step peaklabels uv run cscan path peak-labels

echo "labels present now:"
q "SELECT 'fwd_ret_5d: ' || count(*) FILTER (WHERE fwd_ret_5d IS NOT NULL) || ' / ' || count(*) FROM events WHERE split_key='train' AND in_trade;"
q "SELECT 'peak_ret_5d: ' || count(*) FILTER (WHERE peak_ret_5d IS NOT NULL) || ' / ' || count(*) FROM events WHERE split_key='train' AND in_trade;"

echo "=== LABELS DONE ==="
