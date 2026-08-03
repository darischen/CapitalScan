# Session 10 (Tasks 10.2–10.4): Path Backfill, Derived Labels, Reconciliation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate the `path` table (10.1's schema) for every existing event, build a read-only derived layer that recomputes Session 9's existing event labels purely from `path` + event metadata, and reconcile the two so Session 10's gate (`docs/session10.md` §4) can pass on item 2 ("zero unexplained differences").

**Architecture:** Three new modules. `core/returns.py` gains two pure, IO-free functions (`path_for_event`, `entry_offset_for`) that both the backfill and the derived layer build on — one implementation, reused twice, per CLAUDE.md invariant 2. `research/path_backfill.py` does the IO for 10.2 (read `events`+`bars`, write `path`+`events.fwd_window_days`). `research/path_labels.py` is 10.3's pure derivation plus a thin read-only IO wrapper — **it never writes**, because `docs/session10.md` §6/§7 require the old label columns to stay untouched until the gate passes. `research/path_reconcile.py` is 10.4: it calls 10.3's function, diffs the result against the real `events` columns for the run of record, and reports.

**Tech Stack:** pandas, SQLAlchemy Core (`capitalscan.jobs.db_io`), pytest (unit only — never `tests/integration`), Typer CLI (`capitalscan.jobs.cli`).

## Global Constraints

- **Never run bare `pytest`.** Only `uv run pytest capitalscan/tests/unit capitalscan/tests/property`. Do not touch `capitalscan/tests/integration/`.
- **`core/` performs no IO** (invariant 1). `core/returns.py` additions must not open a connection, read a file, or touch the clock.
- **One signal/label implementation** (invariant 2). Reuse `core.returns.mfe_mae`, `core.returns.realized_return`, and `research.enrich._pct_suffix` — do not re-derive any of them.
- **No magic numbers outside `core/config.py`** (invariant 9). The path window width is `max(StatsParams.fwd_ret_horizons)` (currently 10), never a literal `10`. Reachability window width is `ExitParams.max_hold_days`, never a literal `5`.
- **Never fill, forward-fill, or interpolate a null** (invariant 4). A day with no available bar gets no row — never a padded/zero value.
- **Every generated row carries `run_id` and `git_sha`** (invariant 6) — *does not apply to `path` rows*, which the already-committed 10.1 schema keys only by `(event_id, day_offset)` with no provenance columns of their own; provenance is inherited transitively via `event_id -> events.run_id`. Flag this to the user once; do not "fix" it by altering the 10.1 migration.
- **Old label columns on `events` (`mfe`, `mae`, `touched_*pct`, `fwd_ret_*d`, ...) must not be written by any code in this plan.** 10.3 returns an in-memory `DataFrame`, never an UPDATE/upsert against those columns. This is what makes rollback free (`docs/session10.md` §6–§7).
- **Price series discipline (DESIGN §2.2, CLAUDE.md price-series table):** `path.favorable`/`path.adverse`/`path.terminal` all use **split-adjusted** OHLC (`bars.high`/`low`/`close`), matching `mfe_mae`'s convention — not `adj_close`. This is a deliberate departure from `fwd_ret_*d` (which uses total-return `adj_close`, self-referential to the entry bar's own close rather than anchored to `entry_price`). Task 3 documents this as an anticipated, explained reconciliation difference for the `fwd_ret_*d` family — not a bug to paper over.
- **Day-offset anchor (DESIGN §5.7b, already committed):** `path.day_offset` counts trading days **from `signal_date`**, 1-based. Session 9's own windows (`mfe_mae`'s `[t+1, exit_idx]`, reachability's `[t+1, t+max_hold_days]`) count from **`entry_date`** instead. The two anchors coincide (`entry_offset = 0`) for `TOUCH`/`TOUCH_5M`/`TOUCH_30M` (same-day fill) and differ by exactly one trading day (`entry_offset = 1`) for `NEXT_OPEN` — this is derivable purely from `entry_kind`, never from a price lookup, because `NEXT_OPEN` always fills exactly one trading day after the signal by construction (`core.returns.entry_price_for`'s own docstring). `entry_offset_for(entry_kind)` encodes this rule once.
- **Reconciliation target:** the Session 9 "run of record" — `run_id=backtest_20260802T183304_6b1c5b52`, `config_hash=3e598c59e7d71eae` (the Phase 3 gate run, `ExitParams.max_hold_days=5`, `ExitParams` ATR stop k=1.5/target 4%/`NEXT_OPEN`, per `docs/RESULTS.md`). Task 4 reconciles against this run specifically, not every historical sweep row — matching `docs/session10.md`'s phrase "session 9 labels" (singular, the accepted output), and avoiding the need to know every sweep config's `max_hold_days` from the database.

---

## File Structure

- **Modify:** `capitalscan/core/returns.py` — add `entry_offset_for(entry_kind: EntryKind) -> int` and `path_for_event(entry_price: float, side: Side, fwd_bars: pd.DataFrame) -> pd.DataFrame`. Pure, no IO.
- **Test:** `capitalscan/tests/unit/test_returns_path.py` — new file (avoids colliding with the existing `test_backtest_path.py`, which tests `research/enrich.py::path_metrics` despite its name).
- **Create:** `capitalscan/research/path_backfill.py` — Task 10.2. `build_path_for_event(...)` (thin per-event orchestration) and `run_path_backfill(engine, config, quiet=False) -> PathBackfillReport` (loops tickers, reads `events`+`bars`, upserts `path`, updates `events.fwd_window_days`).
- **Test:** `capitalscan/tests/unit/test_path_backfill.py` — new file. Unit-level: constructs in-memory `bars`/`events` frames, does not touch a real database.
- **Create:** `capitalscan/research/path_labels.py` — Task 10.3. `derive_labels_from_path(...)` (pure) and `derive_session9_labels(engine, config, run_id) -> pd.DataFrame` (read-only IO wrapper).
- **Test:** `capitalscan/tests/unit/test_path_labels.py` — new file.
- **Create:** `capitalscan/research/path_reconcile.py` — Task 10.4. `ReconciliationReport` dataclass and `reconcile(engine, config, run_id) -> ReconciliationReport`.
- **Test:** `capitalscan/tests/unit/test_path_reconcile.py` — new file.
- **Modify:** `capitalscan/jobs/cli.py` — add a `path_app` Typer sub-app (`cscan path backfill`, `cscan path reconcile`), mounted the same way `positions_app`/`db_app` already are.
- **Modify:** `docs/RESULTS.md` — append the Task 10.4 reconciliation findings (required by that task's acceptance criteria).

**Interfaces (what later tasks consume from earlier ones):**

- Task 1 produces `core.returns.entry_offset_for(entry_kind: EntryKind) -> int` and `core.returns.path_for_event(entry_price: float, side: Side, fwd_bars: pd.DataFrame) -> pd.DataFrame` with columns `["day_offset", "favorable", "adverse", "terminal"]`, `day_offset` 1-based starting from the first row of `fwd_bars`.
- Task 2 produces `research.path_backfill.run_path_backfill(engine, config, quiet=False) -> PathBackfillReport` (dataclass: `events_processed: int`, `rows_written: int`, `run_id: str`). Populates the real `path` table and `events.fwd_window_days`.
- Task 3 produces `research.path_labels.derive_session9_labels(engine, config, run_id: str) -> pd.DataFrame`, one row per `event_id`, columns exactly matching the Session 9 label-family columns on `events` (`mfe, mae, time_to_mfe, capture_ratio, touched_2pct, day_touched_2pct, touched_3pct, day_touched_3pct, touched_5pct, day_touched_5pct, touched_10pct, day_touched_10pct, fwd_ret_1d, fwd_ret_2d, fwd_ret_3d, fwd_ret_5d, fwd_ret_10d`), plus `event_id`. **Writes nothing.**
- Task 4 consumes Task 3's function and produces `research.path_reconcile.reconcile(engine, config, run_id: str) -> ReconciliationReport` (dataclass: `total_events: int`, `mismatches: dict[str, pd.DataFrame]` keyed by column name, `explained: dict[str, str]` — a human-readable cause per column that has a known systematic difference).

---

## Task 1: Core path-building and label-derivation primitives

**Files:**
- Modify: `capitalscan/core/returns.py`
- Test: `capitalscan/tests/unit/test_returns_path.py`

**Interfaces:**
- Produces: `entry_offset_for(entry_kind: EntryKind) -> int`, `path_for_event(entry_price: float, side: Side, fwd_bars: pd.DataFrame) -> pd.DataFrame`.

- [ ] **Step 1: Write the failing tests**

```python
# capitalscan/tests/unit/test_returns_path.py
from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core.returns import entry_offset_for, path_for_event
from capitalscan.core.types import EntryKind, Side


@pytest.mark.parametrize(
    "kind,expected",
    [
        (EntryKind.TOUCH, 0),
        (EntryKind.TOUCH_5M, 0),
        (EntryKind.TOUCH_30M, 0),
        (EntryKind.NEXT_OPEN, 1),
    ],
)
def test_entry_offset_for(kind, expected):
    assert entry_offset_for(kind) == expected


def _bars(rows):
    # rows: list of (high, low, close)
    return pd.DataFrame(rows, columns=["high", "low", "close"])


def test_path_for_event_long_day_offsets_are_1_based():
    fwd_bars = _bars([(110, 95, 105), (120, 100, 115), (108, 90, 95)])
    path = path_for_event(entry_price=100.0, side=Side.LONG, fwd_bars=fwd_bars)
    assert list(path["day_offset"]) == [1, 2, 3]


def test_path_for_event_long_favorable_adverse_terminal_match_per_bar_formula():
    fwd_bars = _bars([(110, 95, 105)])
    path = path_for_event(entry_price=100.0, side=Side.LONG, fwd_bars=fwd_bars)
    row = path.iloc[0]
    assert row["favorable"] == pytest.approx((110 - 100) / 100)
    assert row["adverse"] == pytest.approx((95 - 100) / 100)
    assert row["terminal"] == pytest.approx((105 - 100) / 100)


def test_path_for_event_short_flips_sign_like_realized_return():
    fwd_bars = _bars([(110, 95, 105)])
    path = path_for_event(entry_price=100.0, side=Side.SHORT, fwd_bars=fwd_bars)
    row = path.iloc[0]
    # Short: favorable is price going DOWN, adverse is price going UP.
    assert row["favorable"] == pytest.approx((100 - 95) / 100)
    assert row["adverse"] == pytest.approx((100 - 110) / 100)
    assert row["terminal"] == pytest.approx(-(105 - 100) / 100)


def test_path_for_event_mfe_is_unclamped_negative_when_price_never_recovers():
    # ADR 089: MFE is not clamped at zero.
    fwd_bars = _bars([(99, 90, 92)])
    path = path_for_event(entry_price=100.0, side=Side.LONG, fwd_bars=fwd_bars)
    assert path.iloc[0]["favorable"] < 0


def test_path_for_event_empty_fwd_bars_returns_empty_frame_never_padded():
    empty = _bars([])
    path = path_for_event(entry_price=100.0, side=Side.LONG, fwd_bars=empty)
    assert list(path.columns) == ["day_offset", "favorable", "adverse", "terminal"]
    assert len(path) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest capitalscan/tests/unit/test_returns_path.py -v`
Expected: FAIL with `ImportError: cannot import name 'entry_offset_for'` (or `path_for_event`).

- [ ] **Step 3: Implement `entry_offset_for` and `path_for_event` in `core/returns.py`**

Add after `mfe_mae` (which both functions reuse, per invariant 2):

```python
def entry_offset_for(entry_kind: EntryKind) -> int:
    """Trading-day offset of an entry fill from its signal date.

    `TOUCH`, `TOUCH_5M`, and `TOUCH_30M` all fill on the signal bar itself
    (offset 0). `NEXT_OPEN` fills exactly one trading day later, always —
    never more, because `entry_price_for`'s own `NEXT_OPEN` branch either
    returns the very next bar's open or, if there is no next bar, `NaN`
    (a position that never filled, which never reaches the path table at
    all). This makes the offset a pure function of `entry_kind`, needing
    no price lookup — the path table's `day_offset` column (DESIGN §5.7b)
    counts from the signal date, not the entry date, and this is the
    translation between the two anchors every path-derived, entry-anchored
    quantity (MFE, MAE, reachability, `fwd_ret_*d`) needs.
    """
    return 1 if entry_kind is EntryKind.NEXT_OPEN else 0


def path_for_event(entry_price: float, side: Side, fwd_bars: pd.DataFrame) -> pd.DataFrame:
    """Per-day forward path (DESIGN §5.7b): one row per trading day after
    whatever date `fwd_bars` starts at (the caller decides — Task 10.2's
    caller passes bars starting the day after `signal_date`), with
    direction-neutral `favorable`/`adverse` extremes and a `terminal`
    mark, all anchored to `entry_price` and expressed as a return
    fraction.

    Reuses `mfe_mae` one bar at a time (rather than a second hand-rolled
    `(high - entry) / entry` here) and `realized_return` for the terminal
    mark — invariant 2, one implementation. `mfe_mae` on a single-row
    window degenerates to exactly the per-bar formula: max/min over one
    element is that element.

    Split-adjusted OHLC only (`high`, `low`, `close`) — never `adj_close`.
    This intentionally does not match `forward_returns`' total-return
    convention; see the plan header ("Price series discipline") for why.

    `day_offset` is 1-based and contiguous, matching the row's position in
    `fwd_bars` — never padded past however many rows `fwd_bars` actually
    has (invariant 4: a short window near the end of price history yields
    fewer rows, never a filled/interpolated one).
    """
    columns = ["day_offset", "favorable", "adverse", "terminal"]
    if len(fwd_bars) == 0:
        return pd.DataFrame(columns=columns)

    rows: list[dict] = []
    for i in range(len(fwd_bars)):
        one_bar = fwd_bars.iloc[i : i + 1]
        favorable, adverse, _ = mfe_mae(entry_price, side, one_bar)
        terminal = realized_return(entry_price, float(one_bar.iloc[0]["close"]), side)
        rows.append(
            {
                "day_offset": i + 1,
                "favorable": favorable,
                "adverse": adverse,
                "terminal": terminal,
            }
        )
    return pd.DataFrame(rows, columns=columns)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest capitalscan/tests/unit/test_returns_path.py -v`
Expected: PASS, all 7 tests.

- [ ] **Step 5: Run the full fast suite to check for regressions**

Run: `uv run pytest capitalscan/tests/unit capitalscan/tests/property -q`
Expected: PASS, no regressions in `test_returns.py` or elsewhere.

- [ ] **Step 6: Commit**

```bash
git add capitalscan/core/returns.py capitalscan/tests/unit/test_returns_path.py
git commit -m "10.2 prep: add entry_offset_for and path_for_event to core/returns.py"
```

---

## Task 2: Path extraction and backfill (10.2)

**Files:**
- Create: `capitalscan/research/path_backfill.py`
- Test: `capitalscan/tests/unit/test_path_backfill.py`
- Modify: `capitalscan/jobs/cli.py`

**Interfaces:**
- Consumes: `core.returns.path_for_event`, `capitalscan.jobs.db_io.upsert`, `capitalscan.jobs.db_io.get_engine`, `capitalscan.jobs.ingest.run_job` (for `runs` bookkeeping — invariant 6), `capitalscan.jobs.progress.track`, `capitalscan.core.config.Config`/`StatsParams.fwd_ret_horizons`.
- Produces: `build_path_rows_for_event(entry_id, entry_price, side, entry_offset, ticker_bars, signal_date, window_days) -> pd.DataFrame` (pure-ish, takes an already-loaded `ticker_bars` frame — no engine) and `run_path_backfill(engine, config, quiet=False) -> PathBackfillReport`.

- [ ] **Step 1: Write the failing tests for the pure per-ticker windowing helper**

```python
# capitalscan/tests/unit/test_path_backfill.py
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.core.types import EntryKind, Side
from capitalscan.research.path_backfill import (
    fwd_window_for_signal,
    rows_for_event,
)


def _ticker_bars(dates):
    # One row per calendar date given, high/low/close all equal to a
    # simple ramp so tests can assert on exact values.
    data = [
        {"ts": pd.Timestamp(d), "high": 100 + i, "low": 90 + i, "close": 95 + i}
        for i, d in enumerate(dates)
    ]
    frame = pd.DataFrame(data)
    return frame.set_index(frame["ts"], drop=False)


def test_fwd_window_for_signal_returns_up_to_window_days_after_signal():
    dates = [date(2024, 1, i) for i in range(1, 15)]
    bars = _ticker_bars(dates)
    window = fwd_window_for_signal(bars, date(2024, 1, 1), window_days=10)
    assert len(window) == 10
    assert window.index[0] == pd.Timestamp(date(2024, 1, 2))


def test_fwd_window_for_signal_truncates_near_end_of_history_never_pads():
    dates = [date(2024, 1, i) for i in range(1, 5)]
    bars = _ticker_bars(dates)
    window = fwd_window_for_signal(bars, date(2024, 1, 1), window_days=10)
    assert len(window) == 3  # only 3 trading days exist after signal_date


def test_fwd_window_for_signal_raises_if_signal_date_not_in_bars():
    dates = [date(2024, 1, i) for i in range(1, 5)]
    bars = _ticker_bars(dates)
    with pytest.raises(ValueError):
        fwd_window_for_signal(bars, date(2024, 6, 1), window_days=10)


def test_rows_for_event_skips_unfilled_entries():
    dates = [date(2024, 1, i) for i in range(1, 15)]
    bars = _ticker_bars(dates)
    rows, n = rows_for_event(
        event_id=1,
        entry_price=float("nan"),
        side=Side.LONG,
        signal_date=date(2024, 1, 1),
        ticker_bars=bars,
        window_days=10,
    )
    assert rows.empty
    assert n is None


def test_rows_for_event_full_window_sets_fwd_window_days():
    dates = [date(2024, 1, i) for i in range(1, 20)]
    bars = _ticker_bars(dates)
    rows, n = rows_for_event(
        event_id=7,
        entry_price=100.0,
        side=Side.LONG,
        signal_date=date(2024, 1, 1),
        ticker_bars=bars,
        window_days=10,
    )
    assert n == 10
    assert list(rows["event_id"].unique()) == [7]
    assert list(rows["day_offset"]) == list(range(1, 11))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest capitalscan/tests/unit/test_path_backfill.py -v`
Expected: FAIL — `capitalscan.research.path_backfill` does not exist yet.

- [ ] **Step 3: Implement `capitalscan/research/path_backfill.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest capitalscan/tests/unit/test_path_backfill.py -v`
Expected: PASS, all 5 tests.

- [ ] **Step 5: Wire the CLI command**

In `capitalscan/jobs/cli.py`, find where `positions_app`/`db_app` sub-apps are created and mounted (search for `typer.Typer(help=` and `app.add_typer`). Add, near the other sub-apps:

```python
path_app = typer.Typer(help="Forward path store (Session 10)")


@path_app.command("backfill")
def path_backfill_cmd(
    quiet: bool = typer.Option(False, "--quiet", help="JSON-lines progress instead of a live bar"),
) -> None:
    """Populate `path` and `events.fwd_window_days` for every filled entry."""
    from capitalscan.core.config import DEFAULT_CONFIG
    from capitalscan.research.path_backfill import run_path_backfill

    engine = db_io.get_engine()
    with ingest.run_job(engine, "path_backfill", {}) as job_report:
        report = run_path_backfill(engine, DEFAULT_CONFIG, quiet=quiet)
        job_report.rows_written = report.rows_written
    console.print(
        f"path backfill: events_processed={report.events_processed} "
        f"skipped_unfilled={report.events_skipped_unfilled} rows_written={report.rows_written}"
    )


app.add_typer(path_app, name="path")
```

Check the exact names already imported at the top of `capitalscan/jobs/cli.py` (`db_io`, `ingest`, `console`) before pasting — reuse them, do not re-import under different aliases.

- [ ] **Step 6: Run the full fast suite**

Run: `uv run pytest capitalscan/tests/unit capitalscan/tests/property -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add capitalscan/research/path_backfill.py capitalscan/tests/unit/test_path_backfill.py capitalscan/jobs/cli.py
git commit -m "10.2: path extraction and backfill"
```

- [ ] **Step 8: Run the real backfill against the research database and verify by hand**

This step needs the user's go-ahead before running (it writes to the real research DB) and needs `DATABASE_URL_RESEARCH` set. Run:

```
cscan path backfill
```

Then, using the direct `psql` connection from `CLAUDE.md` (`PGPASSWORD=capscan "/c/Program Files/PostgreSQL/18/bin/psql" -h localhost -U capscan -d capitalscan`), verify the 10.2 acceptance criteria:

```sql
-- Row count equals the sum of available forward days, independent of the insert logic.
SELECT
  (SELECT COUNT(*) FROM path) AS path_rows,
  (SELECT COALESCE(SUM(fwd_window_days), 0) FROM events WHERE fwd_window_days IS NOT NULL) AS expected_rows;

-- No event has a gap in its day offsets (every event's offsets are exactly 1..n, contiguous).
SELECT event_id
FROM (
  SELECT event_id, day_offset, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY day_offset) AS rn
  FROM path
) t
WHERE day_offset <> rn
LIMIT 10;
-- Expect zero rows back.

-- Idempotency: rerun `cscan path backfill`, then rerun both checks above — identical output.
```

Then spot-check five events by hand (one near the end of available history, one spanning a holiday week) against raw `bars` rows for the same ticker/dates, confirming `favorable`/`adverse`/`terminal` match `(high-entry)/entry`, `(low-entry)/entry`, `(close-entry)/entry` (or the short-flipped mirror) to the row's own `entry_price`.

Record the row counts and the five hand-checked events in `docs/RESULTS.md` under a new "Session 10 — Task 10.2" heading.

---

## Task 3: Derived layer, existing labels only (10.3)

**Files:**
- Create: `capitalscan/research/path_labels.py`
- Test: `capitalscan/tests/unit/test_path_labels.py`

**Interfaces:**
- Consumes: `core.returns.entry_offset_for`, `core.returns.realized_return`, `research.enrich._pct_suffix` (reused, per `docs/session10.md`'s explicit instruction — not re-derived).
- Produces: `derive_labels_from_path(path, entry_offset, holding_days, entry_price, exit_price, side, max_hold_days, targets, horizons) -> dict` (pure) and `derive_session9_labels(engine, config, run_id) -> pd.DataFrame` (read-only).

- [ ] **Step 1: Write the failing tests for the pure derivation function**

```python
# capitalscan/tests/unit/test_path_labels.py
from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core.types import Side
from capitalscan.research.path_labels import derive_labels_from_path


def _path(rows):
    # rows: list of (day_offset, favorable, adverse, terminal)
    return pd.DataFrame(rows, columns=["day_offset", "favorable", "adverse", "terminal"])


def test_unresolved_position_returns_not_applicable_shape():
    path = _path([(1, 0.01, -0.01, 0.005)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=None,
        entry_price=100.0,
        exit_price=float("nan"),
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02, 0.03, 0.05, 0.10),
        horizons=(1, 2, 3, 5, 10),
    )
    assert out["mfe"] != out["mfe"]  # NaN
    assert out["time_to_mfe"] is None
    assert out["capture_ratio"] is None
    assert out["touched_2pct"] is None
    assert out["day_touched_2pct"] is None


def test_mfe_mae_bounded_by_holding_days_not_full_window():
    # Exit on day 2 (holding_days=2): day 4's bigger favorable move must
    # NOT count toward MFE — this is the exact "different windows" trap
    # docs/session10.md warns about.
    path = _path(
        [
            (1, 0.01, -0.005, 0.01),
            (2, 0.02, -0.01, 0.02),
            (3, 0.03, -0.01, 0.03),
            (4, 0.09, -0.01, 0.09),  # bigger move, but past the exit
        ]
    )
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=2,
        entry_price=100.0,
        exit_price=102.0,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02, 0.03, 0.05, 0.10),
        horizons=(1, 2, 3, 5, 10),
    )
    assert out["mfe"] == pytest.approx(0.02)
    assert out["time_to_mfe"] == 2


def test_reachability_uses_full_max_hold_days_window_past_an_early_exit():
    # Exit on day 1 (holding_days=1), but a day-4 touch of 5% must still
    # register in touched_5pct/day_touched_5pct (reachability window is
    # [1, max_hold_days], independent of exit timing — DESIGN §5.6).
    path = _path(
        [
            (1, 0.01, -0.005, 0.01),
            (2, 0.02, -0.01, 0.02),
            (3, 0.03, -0.01, 0.03),
            (4, 0.06, -0.01, 0.06),
            (5, 0.02, -0.01, 0.02),
        ]
    )
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=1,
        entry_price=100.0,
        exit_price=101.0,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.05,),
        horizons=(1,),
    )
    assert out["touched_5pct"] is True
    assert out["day_touched_5pct"] == 4


def test_next_open_entry_offset_shifts_the_reachability_window():
    # entry_offset=1 (NEXT_OPEN): day_offset 1 is the entry day itself,
    # so the reachability window with max_hold_days=2 must be day_offset
    # in [2, 3], NOT [1, 2].
    path = _path(
        [
            (1, 0.09, -0.01, 0.0),   # entry day itself — must be excluded
            (2, 0.01, -0.01, 0.01),
            (3, 0.02, -0.01, 0.02),
        ]
    )
    out = derive_labels_from_path(
        path=path,
        entry_offset=1,
        holding_days=2,
        entry_price=100.0,
        exit_price=101.0,
        side=Side.LONG,
        max_hold_days=2,
        targets=(0.05,),
        horizons=(1,),
    )
    assert out["touched_5pct"] is False  # the 9% move on day 1 doesn't count


def test_fwd_ret_horizon_reads_terminal_at_entry_offset_plus_horizon():
    path = _path([(1, 0.0, 0.0, 0.005), (2, 0.0, 0.0, 0.02), (3, 0.0, 0.0, 0.03)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=None,
        entry_price=100.0,
        exit_price=float("nan"),
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1, 2, 3),
    )
    assert out["fwd_ret_2d"] == pytest.approx(0.02)


def test_capture_ratio_null_when_mfe_non_positive():
    path = _path([(1, -0.01, -0.02, -0.01)])
    out = derive_labels_from_path(
        path=path,
        entry_offset=0,
        holding_days=1,
        entry_price=100.0,
        exit_price=99.0,
        side=Side.LONG,
        max_hold_days=5,
        targets=(0.02,),
        horizons=(1,),
    )
    assert out["capture_ratio"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest capitalscan/tests/unit/test_path_labels.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `capitalscan/research/path_labels.py`**

```python
"""Session 10 Task 10.3: recompute Session 9's existing event labels from
the `path` table alone. Controlled comparison, not a new computation —
this module must produce the same numbers Session 9's
`research.enrich.path_metrics` produces for the same event, modulo the
one documented exception (`fwd_ret_*d`; see the module-level note below).

**Writes nothing.** `docs/session10.md` §6-§7 require the old label
columns on `events` to stay untouched until the reconciliation gate
(Task 10.4) passes — rollback has to be free. `derive_session9_labels`
returns an in-memory `DataFrame`; nothing in this module executes an
INSERT or UPDATE.

Reads only the `path` table and `events` metadata columns
(`entry_kind`, `holding_days`, `entry_price`, `exit_price`, `side`) —
never `bars`/`indicators` — per `docs/session10.md`'s 10.3 acceptance
criterion.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import Config
from capitalscan.core.returns import entry_offset_for, realized_return
from capitalscan.core.types import EntryKind, Side
from capitalscan.research.enrich import _pct_suffix


def derive_labels_from_path(
    path: pd.DataFrame,
    entry_offset: int,
    holding_days: int | None,
    entry_price: float,
    exit_price: float,
    side: Side,
    max_hold_days: int,
    targets: tuple,
    horizons: tuple,
) -> dict:
    """One event's Session-9-shaped label dict, derived from its `path`
    rows (columns: `day_offset`, `favorable`, `adverse`, `terminal`).

    Two windows, translated from entry-anchored to signal-anchored via
    `entry_offset` (see the plan header, "Day-offset anchor"):

    - MFE/MAE: `day_offset` in `[entry_offset+1, entry_offset+holding_days]`
      — the exact analogue of `mfe_mae`'s `[t+1, exit_idx]`, since
      `holding_days == exit_idx + 1` (`core.exits.ExitResult`'s own
      convention).
    - Reachability: `day_offset` in `[entry_offset+1, entry_offset+max_hold_days]`
      — the full window regardless of `holding_days` (DESIGN §5.6).

    `None`/`NaN` (never a fabricated value) when `holding_days is None`
    — the position never resolved, matching `path_metrics`'s own
    `exit_idx is None` branch exactly.

    `fwd_ret_*d` is the one deliberate exception (see the plan header,
    "Price series discipline"): it reads `path.terminal` at
    `day_offset = entry_offset + horizon`, which is entry-price-anchored,
    split-adjusted-close return — not `forward_returns`'s total-return,
    self-referential-to-entry-bar-close convention. It is still computed
    here, unconditionally, so Task 10.4 has something to diff against and
    document as an explained (not silently ignored) difference.
    """
    by_offset = path.set_index("day_offset")
    out: dict = {}

    if holding_days is None:
        out["mfe"] = float("nan")
        out["mae"] = float("nan")
        out["time_to_mfe"] = None
        out["capture_ratio"] = None
        for target in targets:
            suffix = _pct_suffix(target)
            out[f"touched_{suffix}"] = None
            out[f"day_touched_{suffix}"] = None
    else:
        held_offsets = range(entry_offset + 1, entry_offset + holding_days + 1)
        held = by_offset.loc[by_offset.index.isin(held_offsets)].sort_index()

        if held.empty:
            out["mfe"] = float("nan")
            out["mae"] = float("nan")
            out["time_to_mfe"] = None
            out["capture_ratio"] = None
        else:
            out["mfe"] = float(held["favorable"].max())
            out["mae"] = float(held["adverse"].min())
            peak_offset = held["favorable"].idxmax()
            out["time_to_mfe"] = int(peak_offset - entry_offset)

            r_exit = realized_return(entry_price, exit_price, side)
            out["capture_ratio"] = (
                None if out["mfe"] != out["mfe"] or out["mfe"] <= 0 else float(r_exit / out["mfe"])
            )

        reach_offsets = range(entry_offset + 1, entry_offset + max_hold_days + 1)
        reach = by_offset.loc[by_offset.index.isin(reach_offsets)].sort_index()
        for target in targets:
            suffix = _pct_suffix(target)
            touched_rows = reach.loc[reach["favorable"] >= target]
            if touched_rows.empty:
                out[f"touched_{suffix}"] = False
                out[f"day_touched_{suffix}"] = None
            else:
                first_offset = touched_rows.index.min()
                out[f"touched_{suffix}"] = True
                out[f"day_touched_{suffix}"] = int(first_offset - entry_offset)

    for h in horizons:
        target_offset = entry_offset + h
        if target_offset in by_offset.index:
            out[f"fwd_ret_{h}d"] = float(by_offset.loc[target_offset, "terminal"])
        else:
            out[f"fwd_ret_{h}d"] = float("nan")

    return out


def derive_session9_labels(engine: Engine, config: Config, run_id: str) -> pd.DataFrame:
    """Read-only: one row per `event_id` in `run_id`, columns matching the
    Session 9 label family on `events`. See the module docstring — this
    performs no writes.
    """
    with engine.connect() as conn:
        events = pd.read_sql(
            text(
                "SELECT id AS event_id, entry_kind, holding_days, entry_price, "
                "exit_price, side FROM events WHERE run_id = :run_id"
            ),
            conn,
            params={"run_id": run_id},
        )
        if events.empty:
            return events.assign(**{})
        event_ids = events["event_id"].tolist()
        path = pd.read_sql(
            text(
                "SELECT event_id, day_offset, favorable, adverse, terminal "
                "FROM path WHERE event_id = ANY(:ids)"
            ),
            conn,
            params={"ids": event_ids},
        )

    max_hold_days = config.exits.max_hold_days
    targets = config.stats.reach_targets
    horizons = config.stats.fwd_ret_horizons

    rows = []
    for _, ev in events.iterrows():
        event_path = path.loc[path["event_id"] == ev["event_id"]]
        holding_days = None if pd.isna(ev["holding_days"]) else int(ev["holding_days"])
        labels = derive_labels_from_path(
            path=event_path,
            entry_offset=entry_offset_for(EntryKind(ev["entry_kind"])),
            holding_days=holding_days,
            entry_price=float(ev["entry_price"]),
            exit_price=float(ev["exit_price"]) if not pd.isna(ev["exit_price"]) else float("nan"),
            side=Side(ev["side"]),
            max_hold_days=max_hold_days,
            targets=targets,
            horizons=horizons,
        )
        labels["event_id"] = ev["event_id"]
        rows.append(labels)

    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest capitalscan/tests/unit/test_path_labels.py -v`
Expected: PASS, all 6 tests.

- [ ] **Step 5: Determinism check — add and pass one more test**

```python
def test_derive_labels_from_path_is_deterministic():
    path = _path([(1, 0.01, -0.005, 0.01), (2, 0.03, -0.01, 0.02)])
    kwargs = dict(
        path=path, entry_offset=0, holding_days=2, entry_price=100.0,
        exit_price=102.0, side=Side.LONG, max_hold_days=5,
        targets=(0.02,), horizons=(1, 2),
    )
    first = derive_labels_from_path(**kwargs)
    second = derive_labels_from_path(**kwargs)
    assert first == second
```

Run: `uv run pytest capitalscan/tests/unit/test_path_labels.py -v` — Expected: PASS.

- [ ] **Step 6: Run the full fast suite**

Run: `uv run pytest capitalscan/tests/unit capitalscan/tests/property -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add capitalscan/research/path_labels.py capitalscan/tests/unit/test_path_labels.py
git commit -m "10.3: derived label layer from the path table, existing labels only"
```

---

## Task 4: Reconciliation — session gate (10.4)

**Files:**
- Create: `capitalscan/research/path_reconcile.py`
- Test: `capitalscan/tests/unit/test_path_reconcile.py`
- Modify: `capitalscan/jobs/cli.py`
- Modify: `docs/RESULTS.md`

**Interfaces:**
- Consumes: `research.path_labels.derive_session9_labels`.
- Produces: `ReconciliationReport` dataclass, `reconcile(engine, config, run_id) -> ReconciliationReport`.

- [ ] **Step 1: Write the failing tests using a fake engine-shaped fixture**

Since this module's only new logic is the *diff*, not the read (already tested via `derive_session9_labels` in Task 3), test the diff function directly against two in-memory `DataFrame`s rather than mocking SQL:

```python
# capitalscan/tests/unit/test_path_reconcile.py
from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.research.path_reconcile import diff_labels


def test_diff_labels_no_mismatches_returns_empty_dict():
    derived = pd.DataFrame({"event_id": [1, 2], "mfe": [0.01, 0.02], "mae": [-0.01, -0.02]})
    actual = pd.DataFrame({"event_id": [1, 2], "mfe": [0.01, 0.02], "mae": [-0.01, -0.02]})
    mismatches = diff_labels(derived, actual, columns=["mfe", "mae"])
    assert mismatches == {}


def test_diff_labels_flags_a_numeric_mismatch_outside_tolerance():
    derived = pd.DataFrame({"event_id": [1], "mfe": [0.05]})
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.01]})
    mismatches = diff_labels(derived, actual, columns=["mfe"])
    assert "mfe" in mismatches
    assert list(mismatches["mfe"]["event_id"]) == [1]


def test_diff_labels_tolerates_float_noise_within_1e_minus_9():
    derived = pd.DataFrame({"event_id": [1], "mfe": [0.010000000001]})
    actual = pd.DataFrame({"event_id": [1], "mfe": [0.01]})
    mismatches = diff_labels(derived, actual, columns=["mfe"])
    assert mismatches == {}


def test_diff_labels_flags_boolean_and_null_mismatches():
    derived = pd.DataFrame({"event_id": [1, 2], "touched_2pct": [True, None]})
    actual = pd.DataFrame({"event_id": [1, 2], "touched_2pct": [False, None]})
    mismatches = diff_labels(derived, actual, columns=["touched_2pct"])
    assert list(mismatches["touched_2pct"]["event_id"]) == [1]


def test_diff_labels_both_null_is_not_a_mismatch():
    derived = pd.DataFrame({"event_id": [1], "day_touched_5pct": [None]})
    actual = pd.DataFrame({"event_id": [1], "day_touched_5pct": [None]})
    mismatches = diff_labels(derived, actual, columns=["day_touched_5pct"])
    assert mismatches == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest capitalscan/tests/unit/test_path_reconcile.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `capitalscan/research/path_reconcile.py`**

```python
"""Session 10 Task 10.4: the session gate. Compares Task 10.3's
path-derived labels against the labels Session 9 actually wrote to
`events` for one run, and reports every mismatch rather than silently
accepting or silently "fixing" one — `docs/session10.md`: "Do not adjust
the new layer to match the old one without understanding the cause."

Committed, re-runnable check (not a one-off script): call `reconcile(...)`
from `cscan path reconcile` or from a test/notebook at any time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import Config
from capitalscan.research.path_labels import derive_session9_labels

# Every column Task 10.3 derives, in the order they appear on `events`.
LABEL_COLUMNS = [
    "mfe",
    "mae",
    "time_to_mfe",
    "capture_ratio",
    "touched_2pct",
    "day_touched_2pct",
    "touched_3pct",
    "day_touched_3pct",
    "touched_5pct",
    "day_touched_5pct",
    "touched_10pct",
    "day_touched_10pct",
    "fwd_ret_1d",
    "fwd_ret_2d",
    "fwd_ret_3d",
    "fwd_ret_5d",
    "fwd_ret_10d",
]

# fwd_ret_*d is a known, explained difference (see plan header "Price
# series discipline"): path.terminal is entry-price-anchored,
# split-adjusted-close return; Session 9's fwd_ret_*d is total-return
# (adj_close), self-referential to the entry bar's own close. The two
# measure genuinely different quantities by design, not by defect.
EXPLAINED_COLUMNS = {
    col: (
        "path.terminal uses entry-price-anchored split-adjusted close "
        "(matching mfe_mae's convention); Session 9's fwd_ret_*d uses "
        "total-return adj_close self-referential to the entry bar's own "
        "close (DESIGN §2.2, core/returns.py module docstring). Both are "
        "intentional, different price-series conventions — not a bug."
    )
    for col in ["fwd_ret_1d", "fwd_ret_2d", "fwd_ret_3d", "fwd_ret_5d", "fwd_ret_10d"]
}

_FLOAT_TOL = 1e-9


def diff_labels(derived: pd.DataFrame, actual: pd.DataFrame, columns: list[str]) -> dict[str, pd.DataFrame]:
    """Per-column mismatch frames, keyed by column name. A column with no
    mismatches is absent from the result (never an empty-but-present key,
    so `bool(mismatches)` reads as "any differences at all").

    Both-null is not a mismatch (invariant 4's null convention is shared
    by both sides). Floats compare within `_FLOAT_TOL` absolute tolerance
    — comparing two independently computed float pipelines for bit-exact
    equality would fail on ordinary floating-point round-off, which is not
    what this check exists to catch.
    """
    merged = derived.merge(actual, on="event_id", suffixes=("_derived", "_actual"))
    mismatches: dict[str, pd.DataFrame] = {}
    for col in columns:
        d_col, a_col = f"{col}_derived", f"{col}_actual"
        d_vals, a_vals = merged[d_col], merged[a_col]

        both_null = d_vals.isna() & a_vals.isna()
        if pd.api.types.is_numeric_dtype(d_vals) or pd.api.types.is_numeric_dtype(a_vals):
            diff = (d_vals.astype(float) - a_vals.astype(float)).abs()
            mismatch_mask = ~both_null & (diff.isna() | (diff > _FLOAT_TOL))
        else:
            mismatch_mask = ~both_null & (d_vals != a_vals)

        if mismatch_mask.any():
            mismatches[col] = merged.loc[mismatch_mask, ["event_id", d_col, a_col]]
    return mismatches


@dataclass
class ReconciliationReport:
    run_id: str
    total_events: int
    mismatches: dict[str, pd.DataFrame] = field(default_factory=dict)
    explained: dict[str, str] = field(default_factory=dict)

    @property
    def unexplained_mismatch_columns(self) -> list[str]:
        return [c for c in self.mismatches if c not in self.explained]

    @property
    def passes(self) -> bool:
        return len(self.unexplained_mismatch_columns) == 0


def reconcile(engine: Engine, config: Config, run_id: str) -> ReconciliationReport:
    derived = derive_session9_labels(engine, config, run_id)
    with engine.connect() as conn:
        actual = pd.read_sql(
            text(
                f"SELECT id AS event_id, {', '.join(LABEL_COLUMNS)} FROM events "
                "WHERE run_id = :run_id"
            ),
            conn,
            params={"run_id": run_id},
        )

    mismatches = diff_labels(derived, actual, LABEL_COLUMNS) if not derived.empty else {}
    explained = {col: reason for col, reason in EXPLAINED_COLUMNS.items() if col in mismatches}
    return ReconciliationReport(
        run_id=run_id,
        total_events=len(actual),
        mismatches=mismatches,
        explained=explained,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest capitalscan/tests/unit/test_path_reconcile.py -v`
Expected: PASS, all 5 tests.

- [ ] **Step 5: Wire the CLI command**

In `capitalscan/jobs/cli.py`, add to the `path_app` sub-app created in Task 2:

```python
@path_app.command("reconcile")
def path_reconcile_cmd(
    run_id: str = typer.Option(..., "--run-id", help="events.run_id to reconcile against"),
) -> None:
    """Task 10.4: diff path-derived labels against Session 9's stored labels."""
    from capitalscan.core.config import DEFAULT_CONFIG
    from capitalscan.research.path_reconcile import reconcile

    engine = db_io.get_engine()
    report = reconcile(engine, DEFAULT_CONFIG, run_id)
    console.print(f"reconcile: run_id={run_id} total_events={report.total_events}")
    for col, frame in report.mismatches.items():
        tag = "[yellow]explained[/yellow]" if col in report.explained else "[red]UNEXPLAINED[/red]"
        console.print(f"  {col}: {len(frame)} mismatches {tag}")
    console.print("[green]PASS[/green]" if report.passes else "[red]FAIL[/red]")
```

- [ ] **Step 6: Run the full fast suite**

Run: `uv run pytest capitalscan/tests/unit capitalscan/tests/property -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add capitalscan/research/path_reconcile.py capitalscan/tests/unit/test_path_reconcile.py capitalscan/jobs/cli.py
git commit -m "10.4: reconciliation against session 9 labels"
```

- [ ] **Step 8: Run reconciliation against the real run of record and investigate every mismatch**

Requires Task 2's real backfill to have already run against the research database. Run:

```
cscan path reconcile --run-id backtest_20260802T183304_6b1c5b52
```

For every column reported as `UNEXPLAINED`:
1. Pull a handful of mismatching `event_id`s from the printed frame.
2. Query `path` and `events` directly via `psql` for those ids.
3. Work out the cause against the "Known mismatch causes worth checking first" list in `docs/session10.md` §3 (trading vs calendar day offsets, inclusive/exclusive boundary, intraday extreme vs close, entry price convention, MFE/MAE window vs reachability window, five-day vs ten-day window, look-ahead in the old path).
4. If the cause is a bug in Task 10.2/10.3's code, fix it, rerun the backfill and reconciliation, and confirm the column drops out of `mismatches` entirely.
5. If the cause is a genuine, understood structural difference (like `fwd_ret_*d`), add it to `EXPLAINED_COLUMNS` in `path_reconcile.py` with the same reasoning-first style as the existing entry, and re-run to confirm `report.passes` is `True`.
6. If the investigation concludes Session 9's *stored* value was wrong, do not force the derived layer to match it — record the finding as-is.

Do not proceed past this step until `report.passes` is `True` (`docs/session10.md` §3: "Do not start 10.5 until this task passes clean").

- [ ] **Step 9: Record the outcome in `docs/RESULTS.md`**

Append a "Session 10 — Task 10.4 Reconciliation" section: the run_id reconciled against, `total_events`, the full list of columns checked, which had mismatches, which were explained and why, which were bugs that got fixed (with the fix committed), and confirmation of `report.passes == True`. This satisfies 10.4's acceptance criterion: "Investigation outcomes recorded in `RESULTS.md`, including the case where the old labels were wrong."

- [ ] **Step 10: Final commit**

```bash
git add docs/RESULTS.md
git commit -m "10.4: record reconciliation findings in RESULTS.md"
```

---

## Self-Review Notes (for whoever executes this plan)

- **Spec coverage:** 10.2 (Task 2) covers window sourcing from `StatsParams.fwd_ret_horizons`, trading-day offsets, entry price reuse, price adjustment consistency, intraday-extreme-vs-close split, partial-window handling, idempotency, and progress reporting. 10.3 (Task 3) covers the single re-runnable entry point, determinism, price-history-free reads, and runtime (a pure in-memory computation over however many events exist — no explicit timing test needed given the small per-event cost, but flag to the user if the real run exceeds a few seconds). 10.4 (Task 4) covers the diff, the known-mismatch-cause investigation loop, the committed re-runnable check, and the `RESULTS.md` write-up.
- **Explicitly out of scope for this plan** (per the user's "10.2 to 10.4" framing): 10.5 (new label families), 10.6 (live capture), 10.7 (tests/docs/ADR inventory). Do not start those without a separate plan.
- **Known open design call, flagged rather than silently decided:** whether `path.day_offset` truly anchors to `signal_date` (as `DESIGN.md` §5.7b's committed text says) rather than `entry_date` is load-bearing for every formula in Tasks 2-4. If, once real data is inspected in Task 2 Step 8, this reading looks wrong, stop and raise it before Task 3 — changing the anchor after Task 3/4 are built would invalidate their window-translation math.
