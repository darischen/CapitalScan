"""Rebuild `events` one year at a time, so a stop costs one year and not all of it.

`cscan events --lookback N` runs the whole window in one process: it holds
every hit in memory and writes once at the end, so an interrupted run leaves
zero rows and a `runs` row marked `interrupted`. That is fine for the
nightly five-day window and wrong for a seventeen-year rebuild — the
2026-08-19 attempt ran 2.5 hours with no way to see progress and no partial
credit.

Each year here is its own `run_events` call and its own transaction. Kill it
between years and the finished years stay. Re-run it and completed years
upsert to identical values, because `run_events` is deterministic for a
fixed config and bar set: the natural key is
`(config_hash, ticker, signal_date, signal_type, entry_kind)`, so a repeat
is an update to the same values rather than a duplicate.

**Not a CLI command.** `cscan events` keeps its lookback contract for the
nightly path; this is an operational script for a rebuild, which is a rare
and deliberate act.

    uv run python scripts/rebuild_events_chunked.py
    uv run python scripts/rebuild_events_chunked.py --from 2018 --to 2020

Progress goes to stdout with a per-year row count, so a long run can be
watched. Respect the poller quiet window: nothing may touch the database
between 06:30 and 13:00 PT.
"""

from __future__ import annotations

import argparse
from datetime import date

from capitalscan.jobs import db_io
from capitalscan.jobs.compute import run_events
from capitalscan.jobs.config import resolve_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # 2010 because `SplitParams.event_start` refuses anything earlier —
    # labelling a pre-2010 row `train` is a leakage bug, and the guard fires
    # per row deep inside the build loop rather than up front.
    parser.add_argument("--from", dest="start_year", type=int, default=2010)
    parser.add_argument("--to", dest="end_year", type=int, default=date.today().year)
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated. Defaults to every active ticker.",
    )
    args = parser.parse_args()

    config = resolve_config()
    engine = db_io.get_engine()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        from sqlalchemy import text

        with engine.connect() as conn:
            rows = conn.execute(text("SELECT ticker FROM tickers WHERE is_active")).fetchall()
        tickers = [r[0] for r in rows]

    print(f"{len(tickers)} tickers, {args.start_year}-{args.end_year}", flush=True)

    total = 0
    for year in range(args.start_year, args.end_year + 1):
        start = date(year, 1, 1)
        # A hard 12-31 end rather than "today" for the current year: the
        # nightly job covers the recent window, and a moving end date makes
        # a re-run cover something different from the first run.
        end = date(year, 12, 31)
        report = run_events(tickers, start, end, engine=engine, config=config)
        total += report.rows_written
        print(
            f"  {year}: {report.rows_written:>7,} rows, "
            f"{report.rows_flagged:>6,} null-indicator bars skipped",
            flush=True,
        )

    print(f"total {total:,} rows", flush=True)
    return 0


if __name__ == "__main__":
    # Required on Windows: `ProcessPoolExecutor` uses spawn, so every entry
    # point needs this guard or importing the module re-runs it.
    raise SystemExit(main())
