"""Report active tickers whose daily bars have stopped (BACKLOG, 2026-11-08).

The IO half of `core.stale_tickers`. **Reads only.** It writes nothing but
its own `runs` row, and it never touches `is_active`, `delisted_on` or any
other column: retirement stays a manual decision (ADR 207 for a symbol
change, a plain retirement for a delisting), because a long trading halt
looks the same from here.

**One indexed statement for the bars.** The correlated `max(b.ts)` probes
the `bars` primary key `(ticker, ts, interval)` once per active ticker,
walking back from the newest row, so it costs about the number of active
tickers in index lookups rather than a scan of 4.5M rows. The calendar is a
second, small range read on the `trading_days` primary key.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from capitalscan.core.config import DEFAULT_STALE_TICKERS, StaleTickerParams
from capitalscan.core.stale_tickers import (
    LastBar,
    StaleTicker,
    describe,
    find_stale,
    summarize,
)
from capitalscan.jobs import ingest

JOB = "stale_tickers"

# Daily bars are stamped at the session's midnight UTC (`ingest`), so the
# UTC date of the newest one is the session it belongs to.
LAST_BARS_SQL = """
SELECT t.ticker,
       t.cik,
       (SELECT (max(b.ts) AT TIME ZONE 'UTC')::date
          FROM bars b
         WHERE b.ticker = t.ticker AND b.interval = '1d') AS last_bar
  FROM tickers t
 WHERE t.is_active
 ORDER BY t.ticker
"""

SESSIONS_SQL = "SELECT d FROM trading_days WHERE d > :since AND d <= :as_of ORDER BY d"


def _as_date(v: date | datetime | None) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v


def fetch_last_bars(engine: Engine) -> list[LastBar]:
    """Every active ticker with its CIK and newest daily bar date."""
    with engine.connect() as conn:
        rows = conn.execute(text(LAST_BARS_SQL)).all()
    return [LastBar(r[0], r[1], _as_date(r[2])) for r in rows]


def fetch_sessions(engine: Engine, since: date, as_of: date) -> list[date]:
    """Sessions after `since`, through `as_of`, from the exchange calendar."""
    with engine.connect() as conn:
        rows = conn.execute(text(SESSIONS_SQL), {"since": since, "as_of": as_of}).scalars()
        return [d for d in (_as_date(v) for v in rows) if d is not None]


def run_stale_ticker_report(
    engine: Engine,
    as_of: date,
    params: StaleTickerParams = DEFAULT_STALE_TICKERS,
    echo: Callable[[str], object] = print,
) -> list[StaleTicker]:
    """Find, log and record the stale active tickers as of session `as_of`.

    `runs.notes` carries the count and the first `params.notes_limit`
    tickers; `echo` gets one line per ticker. `rows_written` stays 0,
    because the report writes no rows.
    """
    run_params = {
        "trigger": "nightly",
        "as_of": as_of.isoformat(),
        "stale_after_sessions": params.stale_after_sessions,
    }
    with ingest.run_job(engine, JOB, run_params) as report:
        rows = fetch_last_bars(engine)
        since = min((r.last_bar for r in rows if r.last_bar is not None), default=as_of)
        sessions = fetch_sessions(engine, since, as_of)
        stale = find_stale(rows, sessions, as_of, params)
        report.notes = summarize(stale, params)
        report.rows_written = 0
    echo(f"stale_tickers: {report.notes}")
    for s in stale:
        echo(f"stale_tickers:   {describe(s)}")
    return stale
