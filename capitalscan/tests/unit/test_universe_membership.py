"""Regression tests for the four `universe`/`events` defects fixed here.

No database, no network. Follows the fake-engine pattern from
`test_bars_hourly_checkpoint.py`: real functions run, only IO is stubbed.

DEFECT 1 — `crit_rev_growth` is a permanent `None` stub (no revenue tag
ingested), which used to zero `in_trade` for every ticker because
`is_tradeable` required all five criteria. Fix: `UniverseParams.
required_criteria` excludes `crit_rev_growth`, and `run_universe` passes it
through to `is_tradeable`.

DEFECT 2 — `_sector_median_return` returned `(None, True)` the instant
`sector is None`, skipping the documented "fall back to the universe
median" path entirely. Every row in `tickers.sector` is null today (verified
with a live SELECT), so `crit_rel_return` always failed. Fix: `sector is
None` now takes the same fallback branch as "too few sector peers."

DEFECT 3 — `as_of` is a raw quarter end with no bound on the future, and
`_in_trade`'s zero-match branch fails open. Evaluating a quarter that has
not ended yet writes a mislabeled row that is invisible today and starts
excluding every event once the calendar catches up, silently either way.
Fix: `run_universe` raises `FutureQuarterError` up front.

DEFECT 4 lives in `test_scan_indicator_lag.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest

from capitalscan.core.config import UniverseParams
from capitalscan.jobs import compute

# ---------------------------------------------------------------------------
# DEFECT 1
# ---------------------------------------------------------------------------


class TestRequiredCriteriaDefault:
    def test_default_required_criteria_excludes_rev_growth(self):
        """The honest subset: rev growth has no ingested data behind it."""
        up = UniverseParams()
        assert "crit_rev_growth" not in up.required_criteria
        assert set(up.required_criteria) == {
            "crit_mcap",
            "crit_above_sma200",
            "crit_sma200_slope",
            "crit_rel_return",
        }


def _healthy_ind_row():
    return pd.Series({"close": 100.0, "sma_200": 90.0, "sma200_slope_60": 0.05})


class TestEvaluateUniverseRow:
    """`_evaluate_universe_row` is the pure assembly step `run_universe`
    delegates to — exercised directly so DEFECT 1's wiring is testable
    without a database.
    """

    def test_permanently_null_rev_growth_no_longer_blocks_in_trade(self):
        row = compute._evaluate_universe_row(
            ticker="AAPL",
            as_of=date(2026, 6, 30),
            ind_row=_healthy_ind_row(),
            mcap=3_000e9,
            rel_return=0.60,
            sector_median=0.10,
            rev_growth=None,  # the permanent stub's actual return value
            adv_20d=1e9,
            up=UniverseParams(),
        )
        assert row["crit_rev_growth"] is None, "audit log must still show null, not False"
        assert row["in_trade"] is True, "the other four criteria pass; rev growth is not required"

    def test_a_genuinely_failing_required_criterion_still_blocks(self):
        row = compute._evaluate_universe_row(
            ticker="XYZ",
            as_of=date(2026, 6, 30),
            ind_row=_healthy_ind_row(),
            mcap=50e9,  # below the $200B floor
            rel_return=0.60,
            sector_median=0.10,
            rev_growth=None,
            adv_20d=1e9,
            up=UniverseParams(),
        )
        assert row["crit_mcap"] is False
        assert row["in_trade"] is False

    def test_requiring_all_five_reproduces_the_original_bug(self):
        """Confirms the diagnosis: `required=None` (all five) is what
        zeroed `in_trade` industry-wide, not `evaluate_criteria` itself.
        """
        from capitalscan.core.universe import CRITERIA

        row_all_five = compute._evaluate_universe_row(
            ticker="AAPL",
            as_of=date(2026, 6, 30),
            ind_row=_healthy_ind_row(),
            mcap=3_000e9,
            rel_return=0.60,
            sector_median=0.10,
            rev_growth=None,
            adv_20d=1e9,
            up=UniverseParams(required_criteria=CRITERIA),
        )
        assert row_all_five["in_trade"] is False


# ---------------------------------------------------------------------------
# DEFECT 2
# ---------------------------------------------------------------------------


class _Row:
    """Mimics a SQLAlchemy `Row`: attribute access and tuple indexing both work."""

    def __init__(self, ticker: str):
        self.ticker = ticker

    def __getitem__(self, i):
        return (self.ticker,)[i]

    def __iter__(self):
        return iter((self.ticker,))


class _Result(list):
    def fetchall(self):
        return list(self)


class _FakeConn:
    """Answers the two ticker-list queries `_sector_median_return` issues."""

    def __init__(self, sector_peers: list[str], all_active: list[str]):
        self.sector_peers = sector_peers
        self.all_active = all_active
        self.queries: list[str] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.queries.append(sql)
        if "sector = :sector" in sql:
            return _Result(_Row(t) for t in self.sector_peers)
        return _Result(_Row(t) for t in self.all_active)


class _FakeEngine:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    @contextmanager
    def connect(self):
        yield self._conn


class TestSectorMedianFallback:
    def test_none_sector_falls_back_to_universe_median_instead_of_bailing(self, monkeypatch):
        conn = _FakeConn(sector_peers=[], all_active=["AAPL", "MSFT", "GOOG"])
        engine = _FakeEngine(conn)
        monkeypatch.setattr(
            compute,
            "_rel_return_756d",
            lambda engine, ticker, as_of, lookback: {"AAPL": 0.1, "MSFT": 0.2, "GOOG": 0.3}[ticker],
        )

        median, used_fallback = compute._sector_median_return(engine, None, date(2026, 6, 30), 756)

        assert median == 0.2
        assert used_fallback is True
        # Must never have queried a nonexistent sector name.
        assert not any("sector = :sector" in q and "None" in q for q in conn.queries)

    def test_known_sector_with_enough_peers_does_not_fall_back(self, monkeypatch):
        conn = _FakeConn(sector_peers=["AAPL", "MSFT", "GOOG", "META", "AMZN"], all_active=[])
        engine = _FakeEngine(conn)
        returns = {"AAPL": 0.1, "MSFT": 0.2, "GOOG": 0.3, "META": 0.4, "AMZN": 0.5}
        monkeypatch.setattr(
            compute, "_rel_return_756d", lambda engine, ticker, as_of, lookback: returns[ticker]
        )

        median, used_fallback = compute._sector_median_return(
            engine, "Technology", date(2026, 6, 30), 756
        )

        assert median == 0.3
        assert used_fallback is False


# ---------------------------------------------------------------------------
# DEFECT 3
# ---------------------------------------------------------------------------


class TestFutureQuarterGuard:
    def test_evaluating_a_quarter_that_has_not_ended_raises(self):
        with pytest.raises(compute.FutureQuarterError, match="2026Q3"):
            compute.run_universe("2026Q3", tickers=[], engine=object(), today=date(2026, 8, 1))

    def test_evaluating_a_quarter_that_ended_today_is_allowed(self, monkeypatch):
        """`as_of == today` must not raise: the quarter has just closed."""
        calls = []
        monkeypatch.setattr(
            compute,
            "run_job",
            _no_op_run_job_factory(calls),
        )
        report = compute.run_universe(
            "2026Q2", tickers=[], engine=object(), today=date(2026, 6, 30)
        )
        assert calls == ["universe"]
        assert report.tickers == []

    def test_evaluating_a_completed_past_quarter_is_allowed(self, monkeypatch):
        calls = []
        monkeypatch.setattr(compute, "run_job", _no_op_run_job_factory(calls))
        compute.run_universe("2026Q1", tickers=[], engine=object(), today=date(2026, 8, 1))
        assert calls == ["universe"]


def _no_op_run_job_factory(calls: list[str]):
    from capitalscan.jobs.ingest import IngestReport

    @contextmanager
    def _fake_run_job(engine, job, params):
        calls.append(job)
        yield IngestReport(job=job, run_id="test-run")

    return _fake_run_job
