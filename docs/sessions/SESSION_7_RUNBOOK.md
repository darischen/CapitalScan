# Session 7 Completion Runbook

## Current Status
✓ Backfill through validation gate: In progress (~2 hours)

## Complete Sequence for Session 7

### Step 1: Backfill Data (IN PROGRESS)
```bash
# Running now: backfill --all --start 2015-01-01 --through-validate
# This will:
# - Download daily bars for all 51 tickers
# - Fetch corporate actions (splits, dividends)
# - Fetch market indices (SPX, VIX)
# - Fetch shares outstanding from SEC
# - Validate data quality
# ETA: 2-3 hours

# Once validation gate completes, resume with:
cscan backfill --all --start 2015-01-01 --resume
# This continues with any remaining ingest jobs (earnings, etc.)
```

### Step 2: Compute Indicators
```bash
# Once bars are complete, compute technical indicators
# Using 14 parallel workers (DESIGN §4.5)
cscan indicators --workers 14
# Computes: Bollinger, Stochastic, ATR, RV, SMA, drawdown, etc.
# ETA: 30-60 minutes
```

### Step 3: Evaluate Universe
```bash
# Evaluate membership criteria for current quarter (DESIGN §4.6)
cscan universe --quarter 2026Q3
# Stores: market cap, SMA health, revenue growth, momentum, etc.
# ETA: 10 minutes
```

### Step 4: Detect Events
```bash
# Detect signal confluence events (DESIGN §4.7)
# Reads t-1 indicators, applies signal logic, debounces
cscan events --lookback 16000  # Full range
# ETA: 20-30 minutes
```

### Step 5: Fetch Hourly Bars (Overnight)
```bash
# Download hourly OHLCV for intraday entry timing analysis
# 13 sequential 60-day windows per ticker
cscan bars --hourly --backfill
# ETA: 5-6 hours (run overnight)
# This supports ADR 007 entry timing sweep analysis
```

### Step 6: Validation & Verification
```bash
# Print detailed validation report
cscan validate --report

# Test that scan command returns plausible results
cscan scan --universe trade --date 2026-07-30

# Check indicator coverage (no post-2010 nulls)
# SQL: SELECT COUNT(*) FROM indicators 
#      WHERE ts >= '2010-01-01' AND bb_mid IS NULL
```

### Step 7: Database Backup
```bash
# Export schema (committed to git as schema.sql)
cscan db schema

# Backup to local disk
# pg_dump should go to second local disk (ADR 083)
pg_dump postgresql://capscan:capscan@localhost:5432/capitalscan > \
  /mnt/backup/capitalscan_$(date +%Y%m%d).sql

# Backup to GitHub Release asset (~300 MB compressed)
# gzip the dump and upload monthly
```

### Step 8: Document Results
Update `RESULTS.md` backfill record:
- 51 tickers (testing), full would be 750
- Bar count: ~30K rows (646 bars × 51 tickers approx)
- Date range: 2015-01-01 to 2026-07-30
- Reject counts by rule (expect all at 'flag' level)
- Coverage gaps (any delisted tickers)
- Tickers dropped with reasons

## Expected Results for 51-Ticker Test

### Bars
- ~646 trading days per ticker (since 2015-01-01)
- Total: ~33K daily bars
- Bar rejects: ~300-400 at 'flag' level (pre-2012 quality, price below $1)
- Validation: CLEAN ✓

### Indicators (after compute)
- ~33K indicator rows (one per bar per ticker)
- Columns: Bollinger (mid/upper/lower/%B/width), Stochastic (K/D fast/full, crosses), ATR, RV, SMA, DD
- Coverage: Full range after ~280-day warmup per ticker

### Events (after detection)
- ~1,300-1,400 raw confluences (~4% of 33K bars)
- After debounce (one event per ticker per bound per day): ~500-700 events
- Columns: All detection columns, split_key, entry_kind='touch'

### Hourly Bars (if fetched)
- ~13M rows total (13 × 60-day windows × 6.5 trading hours × 250 days × 51 tickers)
- Used for intraday entry timing analysis (ADR 007)

## Troubleshooting Expected Issues

### Issue: Backfill hangs on certain tickers
- Check logs for "possibly delisted" or timezone errors
- These are non-fatal; backfill continues
- Solution: Data lands in bar_rejects at 'flag' level

### Issue: Indicators have nulls past warmup
- Check: MIN(ts) for each ticker's indicators vs first bar ts
- Min gap should be ceil(max_warmup × 1.6) days
- If nulls exist, check MIN_BARS_FOR_INDICATORS = 280

### Issue: Events are sparse
- Confluence should fire ~4% of days
- If much lower: check signal thresholds, check for null indicators
- If much higher: check for data quality issues in bars

### Issue: Scan command returns no events
- Likely split_key issue or universe filtering
- Check: SELECT COUNT(*) FROM events WHERE split_key = 'validate'
- v_screen hardcodes split_key = 'validate' (ADR 088)

## Files Changed in Session 7

1. `capitalscan/jobs/fetch/yahoo.py` - Fixed namedtuple access in fetch_actions
2. `data/universe_union.csv` - Created 51-ticker test universe
3. `docs/RESULTS.md` - Added backfill record template
4. `docs/SESSION_7_NOTES.md` - Implementation notes & timeline
5. `docs/SESSION_7_RUNBOOK.md` - This file (command sequence)

## Key Metrics to Record

After completion, capture:
- Total execution time for each phase
- Bar count per ticker (min/max/mean)
- Indicator computation time (total, per ticker)
- Events detected (before/after debounce)
- Hourly bars count (if fetched)
- Database size growth

## Performance Baselines

For 51 tickers:
- Backfill: ~2-3 hours (network-bound, rate-limited)
- Indicators: ~45 min (CPU-bound, 14 workers)
- Universe: ~15 min (mostly trivial queries)
- Events: ~30 min (signal path replays history)
- Hourly bars: ~5-6 hours overnight
- Total: ~10-15 hours if run sequentially, ~6-8 hours if hourly runs overnight

For 750 tickers (production):
- Scale roughly 15x linearly
- Backfill: ~30-45 hours
- Indicators: ~11-12 hours
- Hourly bars: ~80+ hours (batch or multi-night)

## Next Session Planning

Once Session 7 backfill completes:

**Session 8 Goals**: Poller & notifications (Phase 2 gate)
- `poll` job: Live band-touch detection
- Notifier: SMTP, Discord, ntfy
- positions & order_intents: User trade tracking
- Live call overlay: Options pricing (ADR 050)

**Session 9 Goals**: Backtest engine (Phase 3 gate)
- research/backtest.py: Entry/exit resolution
- Path metrics: MFE, MAE, time-to-exit
- Sweep: 18 exit configurations
- Statistical validation: Property tests pass

**Phase 4**: Statistics (after Session 9)
- Baseline computation
- Headline grid & statistics
- Effect size with confidence intervals
- Kill criteria evaluation

