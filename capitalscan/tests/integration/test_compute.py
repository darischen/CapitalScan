"""Integration test for the compute layer (BUILD session 6 / Phase 1 gate).

Requires a reachable `DATABASE_URL_RESEARCH`, same convention as
`tests/integration/test_ingest.py`. Network fetchers are mocked; the
Postgres writes are real, because indicators/events read back their own
prior writes across three tables and a mock would just be re-implementing
the SQL.

The acceptance criterion this exercises (BUILD.md session 6):

    cscan scan --ticker TSM --start ... --end ...
    # returns the ... event with correct %B and %K
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from capitalscan.jobs import compute, db_io, ingest
from capitalscan.jobs.fetch import yahoo

TICKER = "TSM"
N_DAYS = 300  # >= MIN_BARS_FOR_INDICATORS (280) plus room for the dip
START = date(2025, 1, 2)

_TRUNCATE_TABLES = [
    "events",
    "bar_rejects",
    "indicators",
    "bars",
    "corporate_actions",
    "market_days",
    "runs",
    "universe",
    "tickers",
    "earnings",
    "shares_outstanding",
]


def _db_reachable() -> bool:
    try:
        engine = db_io.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="DATABASE_URL_RESEARCH is not reachable in this environment"
)


@pytest.fixture()
def engine():
    eng = db_io.get_engine()
    with eng.begin() as conn:
        for table in _TRUNCATE_TABLES:
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
    yield eng
    with eng.begin() as conn:
        for table in _TRUNCATE_TABLES:
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))


def _confluence_low_bars() -> tuple[pd.DataFrame, date]:
    """A flat-ish series that ends in a sharp multi-day dip: the lower
    Bollinger band and the 14-day Stochastic %K both go oversold on the
    same final bar, which is what `detect()` needs to fire `confluence_low`
    rather than a fabricated field.
    """
    rng = np.random.default_rng(7)
    idx = pd.bdate_range(START, periods=N_DAYS)
    noise = rng.normal(0, 0.4, N_DAYS)
    close = 100 + np.cumsum(noise) * 0.05
    close[-6:] = close[-7] - np.array([1.0, 2.5, 4.5, 7.0, 10.0, 13.5])

    high = close + 0.5
    low = close - 0.5
    open_ = close.copy()  # never gap outside [low, high] here; validation isn't under test

    bars = pd.DataFrame(
        {
            "ticker": TICKER,
            "ts": idx,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "adj_close": close,
            "volume": 2_000_000,
            "adj_factor": 1.0,
        }
    )
    return bars, idx[-1].date()


@pytest.fixture()
def signal_date(monkeypatch) -> date:
    bars, target_date = _confluence_low_bars()

    def fake_daily(tickers, start, end):
        out = bars[(bars["ts"].dt.date >= start) & (bars["ts"].dt.date <= end)]
        return out[out["ticker"].isin(tickers)].reset_index(drop=True)

    def fake_actions(ticker):
        return pd.DataFrame(columns=["ticker", "ts", "action_type", "value"])

    monkeypatch.setattr(yahoo, "fetch_bars_daily", fake_daily)
    monkeypatch.setattr(yahoo, "fetch_actions", fake_actions)
    return target_date


def _mark_tradeable(engine, ticker: str = TICKER) -> None:
    """Record a universe evaluation saying `ticker` is tradeable.

    **Required since ADR 129 made `in_trade` fail closed.** It used to
    return `True` when no evaluation existed, so this test detected a
    confluence-low event without ever saying the ticker was in the trade
    universe. Now an absent evaluation means "not tradeable", `run_events`
    stamps `in_trade = false`, and `scan` — which carries the predicate —
    returns nothing. The event is still written, which is why
    `rows_written > 0` kept passing while the assertion below did not.

    Stated explicitly rather than derived by running `run_universe`: this
    test is about signal detection, and making it depend on the health
    filter's four criteria would couple it to a decision it is not
    checking. `as_of` predates `START` so every bar in the window is
    covered.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO universe (ticker, as_of, in_train, in_trade) "
                "VALUES (:ticker, :as_of, true, true) "
                "ON CONFLICT (ticker, as_of) DO UPDATE SET in_trade = true, in_train = true"
            ),
            {"ticker": ticker, "as_of": date(2024, 12, 31)},
        )


def test_scan_returns_the_confluence_low_event_with_correct_pctb_and_k(engine, signal_date):
    ingest.ensure_tickers([TICKER], engine=engine)
    _mark_tradeable(engine)
    bars_report = ingest.run_bars_daily([TICKER], START, signal_date, engine=engine)
    assert bars_report.rows_rejected == 0

    ind_report = compute.run_indicators([TICKER], START, signal_date, engine=engine)
    assert ind_report.rows_written > 0

    events_report = compute.run_events([TICKER], START, signal_date, engine=engine)
    assert events_report.rows_written > 0

    result = compute.scan(tickers=[TICKER], start=START, end=signal_date, engine=engine)
    row = result.loc[result["signal_date"] == signal_date]
    assert not row.empty, "expected a confluence-low event on the engineered dip date"

    hit = row.iloc[0]
    assert hit["side"] == "long"
    assert hit["signal_type"] == "confluence_low"
    assert "confluence_low" in hit["signal_types_all"]
    assert "bb_lower_touch" in hit["signal_types_all"]
    assert "stoch_oversold" in hit["signal_types_all"]
    # %B < 0 means the close sat below the lower band outright — consistent
    # with the 13.5-point one-day drop engineered into the fixture.
    assert hit["bb_pctb"] < 0
    assert hit["k_full"] <= 20.0  # SignalParams.stoch_oversold default


def test_events_job_rerun_is_idempotent(engine, signal_date):
    ingest.ensure_tickers([TICKER], engine=engine)
    ingest.run_bars_daily([TICKER], START, signal_date, engine=engine)
    compute.run_indicators([TICKER], START, signal_date, engine=engine)

    compute.run_events([TICKER], START, signal_date, engine=engine)
    with engine.connect() as conn:
        n_first = conn.execute(text("SELECT count(*) FROM events")).scalar()

    compute.run_events([TICKER], START, signal_date, engine=engine)
    with engine.connect() as conn:
        n_second = conn.execute(text("SELECT count(*) FROM events")).scalar()

    assert n_first == n_second
    assert n_first > 0


def test_events_carry_split_key_and_touch_entry(engine, signal_date):
    ingest.ensure_tickers([TICKER], engine=engine)
    ingest.run_bars_daily([TICKER], START, signal_date, engine=engine)
    compute.run_indicators([TICKER], START, signal_date, engine=engine)
    compute.run_events([TICKER], START, signal_date, engine=engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT split_key, entry_kind, entry_price, entry_date, exit_price "
                "FROM events WHERE ticker = :ticker AND signal_date = :d"
            ),
            {"ticker": TICKER, "d": signal_date},
        ).fetchall()

    assert rows
    for row in rows:
        assert row.split_key in ("train", "validate", "holdout")
        assert row.entry_kind == "touch"
        # v1 scope (ADR 049): TOUCH entry price is same-bar and cheap to
        # compute; the exit engine is Phase 3, so exit_price stays null.
        assert row.exit_price is None


def test_zero_nulls_in_required_indicator_columns_after_warmup(engine, signal_date):
    """BUILD §6.6's warmup assertion, scoped to bars actually past warmup.

    The fixture is only `N_DAYS` long and the longest indicator warmup is
    272 bars, so early rows in the write window are legitimately still
    warming up — this checks the tail near `signal_date`, not the whole
    range, the same way BUILD's real assertion checks "on or after
    2010-01-01" against a ticker with continuous coverage stretching back
    further still.
    """
    ingest.ensure_tickers([TICKER], engine=engine)
    ingest.run_bars_daily([TICKER], START, signal_date, engine=engine)
    compute.run_indicators([TICKER], START, signal_date, engine=engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT count(*) FROM indicators WHERE ticker = :ticker "
                "AND ts >= :cutoff "
                "AND (bb_lower IS NULL OR k_full IS NULL OR atr_14 IS NULL)"
            ),
            {"ticker": TICKER, "cutoff": signal_date - pd.Timedelta(days=20)},
        ).scalar()
    assert rows == 0
