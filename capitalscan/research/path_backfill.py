"""Session 10 Task 10.2: populate the `path` table (DESIGN §5.7b) for
every existing event from price history already in the research store.

Nothing here recomputes an entry price — `events.entry_price` already
holds Session 9's resolved fill, slippage included, and reusing it
(rather than calling `core.returns.entry_price_for` a second time) is
`docs/session10.md`'s explicit instruction: "The entry price definition
must match whatever session 9 uses. Read the existing code and reuse it
rather than reimplementing." Reading the stored value is the only way to
guarantee that, since a fresh call could drift from whatever config
produced the stored row.

An event with `entry_price` NULL never filled — a pre-2024 hourly kind,
or a terminal-bar `NEXT_OPEN` — and gets no path rows at all
(`fwd_window_days` stays NULL): there is no anchor price to build a
return series from (invariant 4, no fabricated value).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import Config
from capitalscan.core.returns import path_for_event
from capitalscan.core.types import Side
from capitalscan.jobs import db_io
from capitalscan.jobs.progress import track


def fwd_window_for_signal(ticker_bars: pd.DataFrame, signal_date: date, window_days: int) -> pd.DataFrame:
    """The up-to-`window_days` trading-day slice strictly after `signal_date`.

    `ticker_bars` must be indexed by `pd.Timestamp` (one row per trading
    day, sorted) — the same shape `research/backtest.py`'s `ticker_bars`
    already is, and the same "price history defines the calendar" rule
    `docs/session10.md` §3 requires: no independent calendar computation.

    Raises `ValueError` if `signal_date` itself has no bar — the signal
    fired on that bar, so its absence is a caller-side input mismatch
    (mirrors `research.enrich.resolve_entries`'s own guard).
    """
    signal_ts = pd.Timestamp(signal_date)
    if signal_ts not in ticker_bars.index:
        raise ValueError(f"fwd_window_for_signal: no bar for signal_date={signal_date}")
    pos = ticker_bars.index.get_loc(signal_ts)
    return ticker_bars.iloc[pos + 1 : pos + 1 + window_days]


def rows_for_event(
    event_id: int,
    entry_price: float,
    side: Side,
    signal_date: date,
    ticker_bars: pd.DataFrame,
    window_days: int,
) -> tuple[pd.DataFrame, int | None]:
    """One event's `path` rows plus the `fwd_window_days` value to write.

    Returns `(empty_frame, None)` for an entry that never filled — see
    the module docstring. Otherwise returns the rows from
    `core.returns.path_for_event`, tagged with `event_id`, and the row
    count (1-`window_days`) as the completeness flag.
    """
    if pd.isna(entry_price):
        return pd.DataFrame(columns=["event_id", "day_offset", "favorable", "adverse", "terminal"]), None

    fwd_bars = fwd_window_for_signal(ticker_bars, signal_date, window_days)
    path = path_for_event(entry_price=entry_price, side=side, fwd_bars=fwd_bars)
    if path.empty:
        return path.assign(event_id=pd.Series(dtype="int64")), 0
    path = path.assign(event_id=event_id)
    return path[["event_id", "day_offset", "favorable", "adverse", "terminal"]], len(path)


@dataclass
class PathBackfillReport:
    events_processed: int = 0
    events_skipped_unfilled: int = 0
    rows_written: int = 0
    tickers: list[str] = field(default_factory=list)


def run_path_backfill(engine: Engine, config: Config, quiet: bool = False) -> PathBackfillReport:
    """Backfills `path` and `events.fwd_window_days` for every event with a
    filled entry, one ticker at a time (each ticker's `bars` loaded once
    and reused across all of that ticker's events).

    Idempotent: `path` rows are written through `db_io.upsert` with
    `conflict_cols=["event_id", "day_offset"]` — a rerun overwrites the
    same rows with the same values rather than duplicating or erroring.
    `events.fwd_window_days` is written through a plain `UPDATE ... WHERE
    id = :id`, also idempotent by construction.
    """
    window_days = max(config.stats.fwd_ret_horizons) if config.stats.fwd_ret_horizons else 0
    report = PathBackfillReport()

    with engine.connect() as conn:
        tickers = [
            r[0]
            for r in conn.execute(
                text("SELECT DISTINCT ticker FROM events WHERE entry_price IS NOT NULL ORDER BY ticker")
            )
        ]

    for ticker in track(tickers, description="path backfill", quiet=quiet, label="ticker"):
        with engine.connect() as conn:
            bars = pd.read_sql(
                text("SELECT * FROM bars WHERE ticker = :ticker AND interval = '1d' ORDER BY ts"),
                conn,
                params={"ticker": ticker},
            )
            events = pd.read_sql(
                text(
                    "SELECT id, entry_price, side, signal_date FROM events "
                    "WHERE ticker = :ticker AND entry_price IS NOT NULL ORDER BY id"
                ),
                conn,
                params={"ticker": ticker},
            )
        if bars.empty or events.empty:
            continue
        bars["ts"] = pd.to_datetime(bars["ts"]).dt.tz_localize(None)
        ticker_bars = bars.sort_values("ts").set_index("ts", drop=False)

        all_rows: list[pd.DataFrame] = []
        window_updates: list[dict] = []
        for _, ev in events.iterrows():
            signal_date = ev["signal_date"]
            if isinstance(signal_date, pd.Timestamp):
                signal_date = signal_date.date()
            rows, n = rows_for_event(
                event_id=int(ev["id"]),
                entry_price=float(ev["entry_price"]),
                side=Side(ev["side"]),
                signal_date=signal_date,
                ticker_bars=ticker_bars,
                window_days=window_days,
            )
            report.events_processed += 1
            if n is None:
                report.events_skipped_unfilled += 1
                continue
            if not rows.empty:
                all_rows.append(rows)
            window_updates.append({"id": int(ev["id"]), "n": n})

        if all_rows:
            combined = pd.concat(all_rows, ignore_index=True)
            db_io.upsert(engine, "path", combined, conflict_cols=["event_id", "day_offset"])
            report.rows_written += len(combined)
        if window_updates:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE events SET fwd_window_days = :n WHERE id = :id"),
                    window_updates,
                )
        report.tickers.append(ticker)

    return report
