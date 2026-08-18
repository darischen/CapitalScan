"""One-off: fetch the current session's daily bars, which `cscan nightly` skips.

`cli.py::nightly` and `cli.py::bars` both set `end = date.today()` and hand
it to `yf.download(start=..., end=...)`, whose `end` is **exclusive**. Run
after the close on a trading day, they request bars through *yesterday*, so
the session that just ended is never fetched. Neither command exposes an
`--end`, so there is no way to ask for it from the CLI.

This calls `ingest.run_bars_daily` directly with `end = today + 1 day`.
`run_bars_daily` does not clip rows to `end` — it upserts whatever the
fetcher returns — so the extra day costs nothing and pulls today in.

Deliberately *not* a fix. The real repair belongs in
`jobs/fetch/yahoo.py::_download_daily`, making its `end` inclusive to match
every other date range in the codebase (`run_events`, `run_indicators`,
`scan`, `split_key_for`). That is recorded under Open items in
`docs/DECISIONS.md` and left for an explicit decision, because it changes
what data lands on every run.

Idempotent: `bars` upserts on `(ticker, ts, interval)`, so re-running
overwrites with identical values.

    uv run python scripts/fetch_today_daily.py
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text

from capitalscan.jobs import db_io, ingest


def main() -> None:
    engine = db_io.get_engine()

    with engine.connect() as conn:
        tickers = [r[0] for r in conn.execute(text("SELECT ticker FROM tickers ORDER BY ticker"))]
        before = conn.execute(text("SELECT max(ts)::date FROM bars WHERE interval='1d'")).scalar()

    today = date.today()
    # +1 because the fetcher's `end` is exclusive. This is the whole point
    # of the script; without it today is silently absent.
    end = today + timedelta(days=1)
    start = today - timedelta(days=5)

    print(f"tickers      : {len(tickers)}")
    print(f"max bar now  : {before}")
    print(f"requesting   : {start} .. {end} (exclusive end -> through {today})")

    report = ingest.run_bars_daily(tickers, start, end, engine=engine)

    with engine.connect() as conn:
        after = conn.execute(text("SELECT max(ts)::date FROM bars WHERE interval='1d'")).scalar()
        today_rows = conn.execute(
            text("SELECT count(*) FROM bars WHERE interval='1d' AND ts::date = :d"),
            {"d": today},
        ).scalar()

    print(f"rows written : {report.rows_written}")
    print(f"rejected     : {report.rows_rejected}   flagged: {report.rows_flagged}")
    print(f"max bar after: {after}")
    print(f"rows for {today}: {today_rows}")

    if not today_rows:
        print(
            "\nNo rows for today. Either the session has not settled at the "
            "provider yet, or today is not a trading day. Check "
            "`SELECT d FROM trading_days WHERE d = CURRENT_DATE;` before "
            "assuming a defect."
        )


if __name__ == "__main__":
    main()
