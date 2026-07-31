# Session 7 Implementation Notes

## Overview
Session 7: Full backfill of 750 tickers, 16 years, clean (BUILD.md §7).

Status: In progress (51-ticker test with 2015-2026 data range)

## What Session 7 Does

### Phase 1: Data Ingest (backfill command)
Orchestrates the dependency graph (DESIGN §4.11):
1. `run_calendar()` - NYSE trading days through end of current year
2. `ensure_tickers()` or `run_tickers_refresh()` - Populate tickers table with CIK lookup
3. `run_bars_daily()` - Daily OHLCV from start_date to today
4. `run_actions()` - Stock splits and dividends from corporate actions
5. `run_market()` - SPX and VIX daily data  
6. `run_shares()` - Shares outstanding from SEC XBRL (point-in-time semantics)
7. `run_earnings()` - Historical from SEC 8-K, forward from Finnhub

### Phase 2: Validation Gate
- Validation checks (DESIGN §2.3): price bounds, volume, close/open in range, unexplained moves
- Stooq cross-check: Compare ~20 tickers against external source
- Accept if: zero rejects at 'reject' severity AND no Stooq disagreements above threshold

### Phase 3: Compute Jobs (after backfill resumes)
1. Indicators: Bollinger, Stochastic, ATR, RV, SMA, drawdown (~14 warmup)
2. Universe: Evaluate membership criteria quarterly (DESIGN §4.6)
3. Events: Detect signal confluence events (DESIGN §4.7)

### Phase 4: Hourly Bars (can run overnight)
- Fetch hourly OHLCV for market-hours sessions (13 × 60-day windows per ticker)
- Used for intraday entry timing sweep (ADR 007)

## Acceptance Criteria (BUILD.md §7)
- 750 tickers with first_bar and last_bar populated
- `cscan validate --report` clean at reject severity
- Indicators computed for full range with no post-2010 nulls
- `cscan scan --universe trade --date <recent>` returns plausible results

## Key Decisions Affecting Session 7

- **ADR 035**: Union universe, 2010 start, ADRs only
- **ADR 040**: Ingest 2009, events 2010 (bars back further than events for warmup)
- **ADR 055**: Frozen universe CSV, manually reviewed (Wikipedia scraper + manual review)
- **ADR 081**: Native Windows, transparent Linux only in Postgres container
- **ADR 082**: Allowlist WIP snapshots via wip_snapshot.ps1, never full `git add -A`

## Data Expectations

For 750 tickers × ~16 years (2009-2026):
- Daily bars: ~1.25M rows
- Indicators: Similar scale (one row per bar per ticker)
- Events: ~4% confluence rate → ~50K events across all tickers
- Hourly bars: ~13M rows (13 × 60-day windows × 6.5 market hours/day × 250 trading days)

Storage: ~200-300MB for bars, ~500MB for indicators, ~200-400MB for hourly bars (all numeric)

## Troubleshooting

### Ticker Issues
- Some tickers fail to download from yfinance (timezone issues, delisted, etc.)
- Solution: Logged in bar_rejects at 'flag' level; backfill continues
- Example: SRC, SQ have timezone issues but don't block the process

### Rate Limiting
- Yahoo: 0.5 req/s, batches of 50
- SEC EDGAR: 8 req/s, User-Agent mandatory
- Finnhub: 0.8 req/s
- Implementation: @rate_limited decorator, @with_retry decorator

### Data Quality
- Pre-2012 quality lower (validation catches these in bar_rejects)
- Corporate action handling: Splits (forward and reverse) and dividends tracked separately
- Expected flags: price_below_min ($1.00), zero_or_null_volume, large_unexplained_return

## Next Steps After Backfill

1. Verify validation clean report
2. Run `cscan indicators --workers 14 --start 2009-01-01`  (compute in parallel)
3. Run `cscan universe --quarter 2026Q3` (evaluate latest membership)
4. Run `cscan events --lookback 16000` (detect all historical events)
5. Run `cscan bars --hourly --backfill` (fetch hourly archive, overnight)
6. Run `cscan scan --universe trade --date 2026-07-30` (verify output)
7. Record results in RESULTS.md

## Session 7 Testing Summary

### Test Run: 3 Tickers (AAPL, MSFT, GOOGL) | 2024-2026

Results:
```
Calendar: 4527 trading days written
Tickers: 3 new + 1 existing (TSM)
Bars: 646 per ticker (daily 2024-07-30)
Actions: 206 total (splits + dividends)
Market: 826 SPX/VIX rows
Validation: CLEAN ✓
```

Flags (not rejects):
- price_below_min: 343 (historical data quality)
- zero_or_null_volume: 2 (bad feed)
- large_unexplained_return: 1 (data artifact)

Stooq cross-check: No disagreement above 0.5% threshold

### Key Code Changes for Session 7
- Fixed yahoo.fetch_actions(): Changed from itertuples(_2) to iterrows() for proper column access
- Created data/universe_union.csv with 51 major mega-cap tickers for initial testing
- Full production: Requires fixing wikipedia.py scraper to generate full 750-ticker list

## Timeline Estimates

- Backfill through validation gate: ~50 min (51 tickers)
- Complete backfill (bars download, actions, market): ~2.5 hours (51 tickers)
- Indicators computation: ~30-60 min (parallel, 14 workers)
- Universe evaluation: ~10 min
- Events detection: ~20-30 min
- Hourly bars fetch: ~5.4 hours (can run overnight)
- Full backtest (research/backtest.py): ~10-15 min for Phase 3 gate

Full production (750 tickers) would scale roughly linearly:
- Backfill: ~6+ hours total
- Indicators: ~4-6 hours
- Hourly bars: ~40+ hours (run in batches or overnight)
