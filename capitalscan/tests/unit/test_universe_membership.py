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
from datetime import date, timedelta

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


# ---------------------------------------------------------------------------
# PERF: run_universe's O(n^2) query pattern (controller-added task)
#
# `_sector_median_return` is pure in `(sector, as_of, lookback_days)`, and
# `_rel_return_756d` is pure in `(ticker, as_of, lookback_days)`. Within one
# `run_universe` call both are constant-shaped: `as_of` and
# `up.rel_return_lookback_days` never change across the ticker loop, and
# `tickers.sector` is null for every row today, so every ticker takes the
# same "median over ALL active tickers" fallback branch. Recomputing that
# fallback (up to N `_rel_return_756d` queries) once per ticker is what made
# a 620-ticker quarter cost ~385k queries. These tests pin the fix: same
# answers, linear query count.
# ---------------------------------------------------------------------------


class TestRelReturnMemoization:
    """`_rel_return_756d`'s optional `cache` kwarg must not change the
    answer, must serve repeat `(ticker, as_of, lookback_days)` lookups
    without re-querying, and must distinguish a cached `None` (a real
    "insufficient history" answer) from "not yet cached".
    """

    def _engine_with_counter(self, rows_by_ticker: dict[str, list[tuple]]):
        calls = {"n": 0}

        class _Conn:
            def execute(self, stmt, params=None):
                calls["n"] += 1
                ticker = params["ticker"]
                data = rows_by_ticker.get(ticker, [])
                n = params["n"]
                return _Result(_BarRow(ts, px) for ts, px in data[:n])

        class _Engine:
            @contextmanager
            def connect(self):
                yield _Conn()

        return _Engine(), calls

    def test_cached_result_equals_uncached_result(self):
        """The memoized path must be a pure speedup: identical answer."""
        rows = {"AAPL": [(date(2026, 6, 30) - timedelta(days=i), 100.0 + i) for i in range(760)]}
        as_of = date(2026, 6, 30)

        engine_a, _ = self._engine_with_counter(rows)
        uncached = compute._rel_return_756d(engine_a, "AAPL", as_of, 756)

        engine_b, _ = self._engine_with_counter(rows)
        cache: dict = {}
        cached = compute._rel_return_756d(engine_b, "AAPL", as_of, 756, cache=cache)

        assert cached == uncached
        assert cached is not None

    def test_second_call_with_same_key_does_not_requery(self):
        rows = {"AAPL": [(date(2026, 6, 30) - timedelta(days=i), 100.0 + i) for i in range(760)]}
        engine, calls = self._engine_with_counter(rows)
        cache: dict = {}
        as_of = date(2026, 6, 30)

        first = compute._rel_return_756d(engine, "AAPL", as_of, 756, cache=cache)
        assert calls["n"] == 1
        second = compute._rel_return_756d(engine, "AAPL", as_of, 756, cache=cache)

        assert calls["n"] == 1, "second lookup with an identical key must be served from cache"
        assert second == first

    def test_cached_none_is_not_requeried(self):
        """A ticker with too little history returns None. That None is a
        real, cacheable answer, not an absence -- a truthiness check on the
        cache (`if cache.get(key):`) would wrongly requery every time.
        """
        rows = {"THIN": [(date(2026, 6, 30), 100.0)]}  # far short of 757 bars
        engine, calls = self._engine_with_counter(rows)
        cache: dict = {}
        as_of = date(2026, 6, 30)

        first = compute._rel_return_756d(engine, "THIN", as_of, 756, cache=cache)
        assert first is None
        assert calls["n"] == 1

        second = compute._rel_return_756d(engine, "THIN", as_of, 756, cache=cache)
        assert second is None
        assert calls["n"] == 1, "a cached None must still short-circuit the query"

    def test_different_as_of_is_a_different_cache_key(self):
        """Omitting `as_of` from the key would serve a stale historical
        answer to a different quarter -- the exact bug the task warns about.
        """
        rows = {
            "AAPL": [(date(2026, 6, 30) - timedelta(days=i), 100.0 + i) for i in range(760)]
        }
        engine, calls = self._engine_with_counter(rows)
        cache: dict = {}

        compute._rel_return_756d(engine, "AAPL", date(2026, 6, 30), 756, cache=cache)
        assert calls["n"] == 1
        compute._rel_return_756d(engine, "AAPL", date(2026, 3, 31), 756, cache=cache)
        assert calls["n"] == 2, "a different as_of must not be served from another quarter's entry"


class _BarRow:
    def __init__(self, ts, adj_close):
        self.ts = ts
        self.adj_close = adj_close
        self.close = adj_close
        self.volume = 1_000_000


class TestSectorMedianMemoization:
    def test_cached_result_equals_uncached_result(self):
        active = ["AAPL", "MSFT", "GOOG"]
        returns = {"AAPL": 0.1, "MSFT": 0.2, "GOOG": 0.3}
        as_of = date(2026, 6, 30)

        conn = _FakeConn(sector_peers=[], all_active=active)
        engine = _FakeEngine(conn)

        def fake_rel_return(engine, ticker, as_of, lookback, cache=None):
            return returns[ticker]

        import capitalscan.jobs.compute as compute_mod

        orig = compute_mod._rel_return_756d
        compute_mod._rel_return_756d = fake_rel_return
        try:
            uncached = compute._sector_median_return(engine, None, as_of, 756)
            cached = compute._sector_median_return(engine, None, as_of, 756, cache={})
        finally:
            compute_mod._rel_return_756d = orig

        assert cached == uncached

    def test_second_call_with_same_key_does_not_requery_ticker_list(self, monkeypatch):
        conn = _FakeConn(sector_peers=[], all_active=["AAPL", "MSFT", "GOOG"])
        engine = _FakeEngine(conn)
        monkeypatch.setattr(
            compute,
            "_rel_return_756d",
            lambda engine, ticker, as_of, lookback, cache=None: {
                "AAPL": 0.1,
                "MSFT": 0.2,
                "GOOG": 0.3,
            }[ticker],
        )
        cache: dict = {}
        as_of = date(2026, 6, 30)

        first = compute._sector_median_return(engine, None, as_of, 756, cache=cache)
        queries_after_first = len(conn.queries)
        assert queries_after_first > 0
        second = compute._sector_median_return(engine, None, as_of, 756, cache=cache)

        assert len(conn.queries) == queries_after_first, "repeat call must not re-list tickers"
        assert second == first


class TestRunUniverseQueryCountIsLinear:
    """The end-to-end regression: `run_universe` over N tickers must issue
    O(N) queries for the rel-return / sector-median work, not O(N^2).

    Before the fix, `_sector_median_return`'s "sector is None -> median over
    all active tickers" fallback recomputed `_rel_return_756d` for every one
    of the N tickers, on every one of the N iterations of `run_universe`'s
    own loop -- N^2 `_rel_return_756d` queries just for that, plus another N
    from the outer loop's own (redundant) `_rel_return_756d(ticker)` call.
    """

    def _make_fake_engine(self, tickers: list[str], lookback_days: int, as_of: date):
        counts: dict[str, int] = {
            "indicator": 0,
            "ticker_meta": 0,
            "shares": 0,
            "rel_return": 0,
            "adv_20d": 0,
            "active_list": 0,
        }
        bar_history = [(as_of - timedelta(days=i), 100.0 + i) for i in range(lookback_days + 5)]

        class _Conn:
            def execute(self, stmt, params=None):
                sql = str(stmt)
                params = params or {}
                if "FROM indicators i" in sql:
                    counts["indicator"] += 1
                    return _OneRow(close=100.0, sma_200=90.0, sma200_slope_60=0.05)
                if "SELECT cik, sector FROM tickers WHERE ticker" in sql:
                    counts["ticker_meta"] += 1
                    return _OneRow(cik=None, sector=None)
                if "FROM shares_outstanding" in sql:
                    counts["shares"] += 1
                    return _OneRow()
                if "adj_close FROM bars" in sql:
                    counts["rel_return"] += 1
                    n = params["n"]
                    return _Result(_BarRow(ts, px) for ts, px in bar_history[:n])
                if "close, volume FROM bars" in sql:
                    counts["adv_20d"] += 1
                    return _Result(_BarRow(ts, px) for ts, px in bar_history[:20])
                if "WHERE is_active" in sql:
                    counts["active_list"] += 1
                    return _Result(_Row(t) for t in tickers)
                raise AssertionError(f"unexpected query: {sql}")

        class _Engine:
            @contextmanager
            def connect(self):
                yield _Conn()

        return _Engine(), counts

    def _run(self, monkeypatch, n_tickers: int):
        from capitalscan.jobs.ingest import IngestReport

        tickers = [f"T{i:04d}" for i in range(n_tickers)]
        as_of = date(2026, 6, 30)
        lookback = 756

        @contextmanager
        def fake_run_job(engine, job, params):
            yield IngestReport(job=job, run_id="test-run")

        monkeypatch.setattr(compute, "run_job", fake_run_job)
        monkeypatch.setattr(compute.db_io, "upsert", lambda *a, **k: len(a[2]))

        engine, counts = self._make_fake_engine(tickers, lookback, as_of)
        up = UniverseParams(rel_return_lookback_days=lookback)
        report = compute.run_universe(
            "2026Q2", tickers=tickers, engine=engine, up=up, today=date(2026, 8, 1)
        )
        assert report.tickers == tickers
        return counts

    def test_query_counts_scale_linearly_not_quadratically(self, monkeypatch):
        counts_10 = self._run(monkeypatch, 10)
        counts_40 = self._run(monkeypatch, 40)

        # Per-ticker helpers stay exactly N (unaffected by this fix).
        assert counts_10["indicator"] == 10
        assert counts_40["indicator"] == 40
        assert counts_10["ticker_meta"] == 10
        assert counts_40["ticker_meta"] == 40

        # The memoized hot path: at most one active-ticker listing and at
        # most N rel-return queries total, however many times
        # `_sector_median_return` is invoked (once per ticker).
        assert counts_10["active_list"] == 1
        assert counts_40["active_list"] == 1
        assert counts_10["rel_return"] <= 10
        assert counts_40["rel_return"] <= 40

        # The tell: quadrupling N (10 -> 40) must not quadruple (16x) the
        # rel-return query count. It must stay linear (4x, i.e. <= 40).
        assert counts_40["rel_return"] <= 4 * counts_10["rel_return"] + 4


class _OneRow:
    def __init__(self, **kw):
        self._kw = kw

    def __getattr__(self, name):
        try:
            return self._kw[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def one_or_none(self):
        return self if self._kw else None
