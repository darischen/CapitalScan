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

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import cast

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import Config
from capitalscan.core.returns import entry_offset_for, path_for_event
from capitalscan.core.types import EntryKind, Side
from capitalscan.jobs import db_io
from capitalscan.jobs.progress import track


def fwd_window_for_signal(
    ticker_bars: pd.DataFrame, signal_date: date, window_days: int
) -> pd.DataFrame:
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
    # The `not in index` guard above plus a unique bar index make this the
    # scalar branch of `get_loc`'s signature; the slice arithmetic below
    # already depends on it.
    pos = cast("int", ticker_bars.index.get_loc(signal_ts))
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
        return pd.DataFrame(
            columns=["event_id", "day_offset", "favorable", "adverse", "terminal"]
        ), None

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
    events_skipped_no_signal_bar: int = 0
    rows_written: int = 0
    tickers: list[str] = field(default_factory=list)


def window_days_for_config(config: Config) -> int:
    """The `path` window size (Task 10.2): `max(fwd_ret_horizons) +
    max(entry_offset_for(k) for k in EntryKind)` — 11, not 10, with the
    default config — not just `max(fwd_ret_horizons)`.

    `path.day_offset` counts from `signal_date`, but `derive_labels_from_path`
    (Task 10.3) reads a horizon's terminal mark at `day_offset = entry_offset
    + horizon` (`core.returns.entry_offset_for`). A `NEXT_OPEN` event has
    `entry_offset=1`, so its `fwd_ret_10d` needs `day_offset=11`. Sizing the
    window at exactly 10 would leave that offset permanently missing from
    `path`, so every `NEXT_OPEN` event's `fwd_ret_10d` would come back
    structurally NaN — not because of the documented price-series difference
    (`path_reconcile.EXPLAINED_COLUMNS`), but because of a real coverage gap.
    The `+ max_entry_offset` pads the window for every entry kind uniformly
    so the slowest (largest-offset) entry kind's full horizon is always
    covered.
    """
    if not config.stats.fwd_ret_horizons:
        return 0
    max_entry_offset = max(entry_offset_for(kind) for kind in EntryKind)
    return int(max(config.stats.fwd_ret_horizons)) + max_entry_offset


def _compute_rows_for_ticker(
    events: pd.DataFrame, ticker_bars: pd.DataFrame, window_days: int
) -> tuple[pd.DataFrame, list[dict], int, int, int]:
    """The per-ticker compute loop, pulled out of `_compute_ticker_path` so
    it is testable against in-memory frames with no database involved.

    Guards against a signal bar that doesn't exist yet in `ticker_bars`
    before calling `rows_for_event` — `events` can include live-poller-
    created rows whose `signal_date` is today, ingested before that day's
    `1d` bar has landed (the EOD bars job runs later, or the market is
    still open). That is a legitimate, temporary "not computable yet"
    state, not a caller-side mismatch: `fwd_window_for_signal`'s own
    `ValueError` is correct for a backtest-generated event (whose
    `signal_date` bar is always present by construction) but wrong to let
    propagate here, where one bad row would otherwise crash an entire
    multi-hour parallel run over a condition that resolves itself the next
    time this job runs. Skipped events are counted separately
    (`events_skipped_no_signal_bar`) from a permanently-unfilled entry
    (`events_skipped_unfilled`), since the two causes and their remedies
    differ — this one is "rerun the job later," not "no action needed."

    Returns `(path_rows, window_updates, events_processed,
    events_skipped_unfilled, events_skipped_no_signal_bar)`.
    """
    empty_rows = pd.DataFrame(
        columns=["event_id", "day_offset", "favorable", "adverse", "terminal"]
    )
    all_rows: list[pd.DataFrame] = []
    window_updates: list[dict] = []
    events_processed = 0
    events_skipped_unfilled = 0
    events_skipped_no_signal_bar = 0
    for _, ev in events.iterrows():
        signal_date = ev["signal_date"]
        if isinstance(signal_date, pd.Timestamp):
            signal_date = signal_date.date()
        events_processed += 1

        entry_price = float(ev["entry_price"])
        if not pd.isna(entry_price) and pd.Timestamp(signal_date) not in ticker_bars.index:
            events_skipped_no_signal_bar += 1
            continue

        rows, n = rows_for_event(
            event_id=int(ev["id"]),
            entry_price=entry_price,
            side=Side(ev["side"]),
            signal_date=signal_date,
            ticker_bars=ticker_bars,
            window_days=window_days,
        )
        if n is None:
            events_skipped_unfilled += 1
            continue
        if not rows.empty:
            all_rows.append(rows)
        window_updates.append({"id": int(ev["id"]), "n": n})

    combined = pd.concat(all_rows, ignore_index=True) if all_rows else empty_rows
    return (
        combined,
        window_updates,
        events_processed,
        events_skipped_unfilled,
        events_skipped_no_signal_bar,
    )


def _events_query_for_ticker(
    ticker: str, window_days: int, incomplete_only: bool
) -> tuple[str, dict]:
    """Pure query-building for `_compute_ticker_path`'s events read, split
    out so the incompleteness filter (Task 10.6) is testable without a
    database or a worker-process round trip.
    """
    query = (
        "SELECT id, entry_price, side, signal_date FROM events "
        "WHERE ticker = :ticker AND entry_price IS NOT NULL"
    )
    params: dict = {"ticker": ticker}
    if incomplete_only:
        query += " AND (fwd_window_days IS NULL OR fwd_window_days < :window_days)"
        params["window_days"] = window_days
    query += " ORDER BY id"
    return query, params


def _compute_ticker_path(
    ticker: str, window_days: int, database_url: str | None, incomplete_only: bool = False
) -> tuple[str, pd.DataFrame, list[dict], int, int, int]:
    """One ticker's path rows and `fwd_window_days` updates, computed with
    no side effects on shared state — runs in a worker process under
    `ProcessPoolExecutor(spawn)` when `max_workers > 1`, and identically
    in-process (one call per ticker) when running serially. Opens its own
    connection via `use_null_pool=True` (CLAUDE.md platform note:
    connections aren't picklable across a spawned process, and a pooled
    connection held by a short-lived worker exhausts `max_connections` on
    the server — same reasoning as `jobs.compute._compute_one_ticker`).

    `incomplete_only` (Task 10.6) restricts the events query to rows whose
    forward window has not yet reached `window_days` — the live-capture
    path touches only events still accumulating, rather than recomputing
    every event's path on every run the way `run_path_backfill` does.
    `NULL` (never captured) counts as incomplete, same as any partial count
    below `window_days`.

    Returns `(ticker, path_rows, window_updates, events_processed,
    events_skipped_unfilled, events_skipped_no_signal_bar)` — the caller
    does the actual writes and report bookkeeping, so this function stays
    a pure "read one ticker, compute its rows" step regardless of how many
    workers are dispatching it.
    """
    engine = db_io.get_engine(database_url, use_null_pool=True)
    events_query, params = _events_query_for_ticker(ticker, window_days, incomplete_only)
    with engine.connect() as conn:
        bars = pd.read_sql(
            text("SELECT * FROM bars WHERE ticker = :ticker AND interval = '1d' ORDER BY ts"),
            conn,
            params={"ticker": ticker},
        )
        events = pd.read_sql(text(events_query), conn, params=params)
    empty_rows = pd.DataFrame(
        columns=["event_id", "day_offset", "favorable", "adverse", "terminal"]
    )
    if bars.empty or events.empty:
        return ticker, empty_rows, [], 0, 0, 0

    bars["ts"] = pd.to_datetime(bars["ts"]).dt.tz_localize(None)
    ticker_bars = bars.sort_values("ts").set_index("ts", drop=False)

    (
        combined,
        window_updates,
        events_processed,
        events_skipped_unfilled,
        events_skipped_no_signal_bar,
    ) = _compute_rows_for_ticker(events, ticker_bars, window_days)
    return (
        ticker,
        combined,
        window_updates,
        events_processed,
        events_skipped_unfilled,
        events_skipped_no_signal_bar,
    )


def _run_path_job(
    engine: Engine,
    tickers: list[str],
    window_days: int,
    quiet: bool,
    max_workers: int,
    incomplete_only: bool,
    description: str,
) -> PathBackfillReport:
    """Shared dispatch/write loop behind both `run_path_backfill` and
    `run_path_capture` (Task 10.6). The two differ only in which tickers
    they're given and whether `_compute_ticker_path` scopes its events
    query — the read/write mechanics (parallel dispatch, per-ticker
    `db_io.upsert`, `fwd_window_days` UPDATE) are identical, so a mismatch
    between the two paths' write behavior can't be introduced by drift.

    Restart safety (10.6 acceptance): every write here is either
    `db_io.upsert` (`ON CONFLICT ... DO UPDATE`, so a rerun overwrites
    rather than duplicates) or a plain `UPDATE ... WHERE id = :id`
    (unconditionally idempotent). Each ticker's writes happen only after
    that ticker's compute step returns, so interrupting the job leaves
    every already-written ticker fully correct and every not-yet-reached
    ticker simply untouched — never a half-written one.
    """
    report = PathBackfillReport()
    database_url = engine.url.render_as_string(hide_password=False)

    def _write_result(
        ticker: str,
        rows: pd.DataFrame,
        window_updates: list[dict],
        events_processed: int,
        events_skipped_unfilled: int,
        events_skipped_no_signal_bar: int,
    ) -> None:
        report.events_processed += events_processed
        report.events_skipped_unfilled += events_skipped_unfilled
        report.events_skipped_no_signal_bar += events_skipped_no_signal_bar
        if not rows.empty:
            db_io.upsert(engine, "path", rows, conflict_cols=["event_id", "day_offset"])
            report.rows_written += len(rows)
        if window_updates:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE events SET fwd_window_days = :n WHERE id = :id"),
                    window_updates,
                )
        report.tickers.append(ticker)

    if max_workers > 1:
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _compute_ticker_path, ticker, window_days, database_url, incomplete_only
                ): ticker
                for ticker in tickers
            }
            for future in track(
                as_completed(futures),
                description=description,
                total=len(futures),
                quiet=quiet,
                label="ticker",
            ):
                _write_result(*future.result())
    else:
        for ticker in track(tickers, description=description, quiet=quiet, label="ticker"):
            _write_result(*_compute_ticker_path(ticker, window_days, database_url, incomplete_only))

    return report


def run_path_backfill(
    engine: Engine, config: Config, quiet: bool = False, max_workers: int = 1
) -> PathBackfillReport:
    """Backfills `path` and `events.fwd_window_days` for every event with a
    filled entry, one ticker at a time (each ticker's `bars` loaded once
    and reused across all of that ticker's events).

    `max_workers > 1` dispatches `_compute_ticker_path` across a spawn-mode
    `ProcessPoolExecutor` — the same parallelization shape
    `jobs.compute.run_indicators` already uses for the same reason (one
    ticker's data is independent of every other's, and the read+compute
    step is what's expensive, not the write). Writes are done here, in the
    controlling process, as each ticker's future completes
    (`as_completed`) — never inside a worker — so `db_io.upsert` batching
    and the progress bar both stay single-threaded and race-free.

    Idempotent: `path` rows are written through `db_io.upsert` with
    `conflict_cols=["event_id", "day_offset"]` — a rerun overwrites the
    same rows with the same values rather than duplicating or erroring.
    `events.fwd_window_days` is written through a plain `UPDATE ... WHERE
    id = :id`, also idempotent by construction.

    See `window_days_for_config` for why the window is 11 days, not 10,
    with the default config.

    Recomputes every event's full path on every run — the right tool for a
    one-off or occasional full rebuild, but not what the nightly schedule
    should call once the event set is large (see `run_path_capture`).
    """
    window_days = window_days_for_config(config)
    with engine.connect() as conn:
        tickers = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT DISTINCT ticker FROM events "
                    "WHERE entry_price IS NOT NULL ORDER BY ticker"
                )
            )
        ]
    return _run_path_job(
        engine,
        tickers,
        window_days,
        quiet,
        max_workers,
        incomplete_only=False,
        description="path backfill",
    )


def run_path_capture(
    engine: Engine, config: Config, quiet: bool = False, max_workers: int = 1
) -> PathBackfillReport:
    """Task 10.6: live path capture — the nightly-scheduled counterpart to
    `run_path_backfill` that accumulates each event's forward path as
    trading days pass, instead of requiring a periodic full backfill.

    Scoped to events whose forward window is still incomplete
    (`fwd_window_days IS NULL OR fwd_window_days < window_days`) — a
    newly detected event picks up one more day of path rows each time
    this runs, until `fwd_window_days` reaches `window_days_for_config`
    and it drops out of scope. `_compute_ticker_path`'s `incomplete_only`
    query filter is what does the scoping; everything downstream
    (`path_for_event`, the `path` upsert, the `fwd_window_days` update) is
    the exact same code `run_path_backfill` uses, so a fully-captured
    event's path is byte-identical to what a full backfill would have
    produced for it (10.6 acceptance).

    Intended to run nightly, after the `events` job, alongside
    `indicators`/`bars` in the existing schedule (DESIGN §9.4) — each run
    only touches tickers with at least one incomplete-window event, so
    cost scales with recent signal volume, not with total event history.
    """
    window_days = window_days_for_config(config)
    with engine.connect() as conn:
        tickers = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT DISTINCT ticker FROM events WHERE entry_price IS NOT NULL "
                    "AND (fwd_window_days IS NULL OR fwd_window_days < :window_days) "
                    "ORDER BY ticker"
                ),
                {"window_days": window_days},
            )
        ]
    return _run_path_job(
        engine,
        tickers,
        window_days,
        quiet,
        max_workers,
        incomplete_only=True,
        description="path capture",
    )
