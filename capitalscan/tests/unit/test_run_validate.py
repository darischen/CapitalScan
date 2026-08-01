"""Unit tests for `jobs.ingest.run_validate` — no DB, no network.

Two defects this locks in:

1. The Stooq cross-check was swallowing every exception identically and
   leaving `clean = True` even when the check never actually ran for a
   single ticker (a total failure of the check is indistinguishable from
   "checked, no disagreement"). `TestStooqCheckVisibility` reproduces the
   real symptom — `stooq.fetch_daily` raising for every sampled ticker —
   and asserts the report surfaces it and `clean` goes False.
2. The missing-bar rule (DESIGN §2.3) was documented in `validate_bars`'s
   docstring as living in `run_validate`, but `run_validate` never queried
   `trading_days`. `TestMissingBarWiring` checks the rule is wired end to
   end and bounded to each ticker's own observed span (DESIGN §4.3).

Pattern follows `test_bars_hourly_checkpoint.py`: a fake engine whose
`connect()` satisfies the call site, with `pd.read_sql` itself monkeypatched
to dispatch on the query text rather than touching a real connection.
"""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd

from capitalscan.jobs import ingest


class _FakeConn:
    pass


class _FakeEngine:
    @contextmanager
    def connect(self):  # noqa: ANN201
        yield _FakeConn()


def _sql_dispatcher(
    reject_counts: pd.DataFrame,
    coverage: pd.DataFrame,
    available: list[str],
    trading_days: pd.DataFrame,
    bar_dates: pd.DataFrame,
    local_by_ticker: dict[str, pd.DataFrame],
):
    def fake_read_sql(query, conn, params=None):  # noqa: ANN001, ANN201
        sql = str(query)
        if "FROM bar_rejects" in sql:
            return reject_counts
        if "GROUP BY ticker ORDER BY ticker" in sql:
            return coverage
        if "SELECT DISTINCT ticker FROM bars" in sql:
            return pd.DataFrame({"ticker": available})
        if "SELECT ticker, ts::date AS d FROM bars" in sql:
            return bar_dates
        if "FROM trading_days" in sql:
            return trading_days
        if "ORDER BY ts DESC LIMIT 60" in sql:
            ticker = params["ticker"]
            return local_by_ticker.get(ticker, pd.DataFrame(columns=["d", "close"]))
        raise AssertionError(f"unexpected query in test: {sql}")

    return fake_read_sql


EMPTY_REJECTS = pd.DataFrame(columns=["rule", "severity", "n"])


def _coverage(tickers: list[str], first: str, last: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": tickers,
            "n_bars": [10] * len(tickers),
            "first_ts": [pd.Timestamp(first)] * len(tickers),
            "last_ts": [pd.Timestamp(last)] * len(tickers),
        }
    )


def _trading_days(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"d": pd.to_datetime(dates)})


def _bar_dates(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame([{"ticker": t, "d": pd.Timestamp(d)} for t, d in rows])


class TestStooqCheckVisibility:
    def test_stooq_raising_for_every_ticker_is_not_silently_clean(self, monkeypatch):
        """Reproduces the real bug: `stooq.fetch_daily` raises for every
        sampled ticker. Before the fix this left `clean = True` because
        `disagreements` stays empty when the check never runs.
        """
        tickers = ["AAPL", "MSFT"]
        days = ["2020-01-02", "2020-01-03"]
        dispatcher = _sql_dispatcher(
            reject_counts=EMPTY_REJECTS,
            coverage=_coverage(tickers, days[0], days[-1]),
            available=tickers,
            trading_days=_trading_days(days),
            bar_dates=_bar_dates([(t, d) for t in tickers for d in days]),
            local_by_ticker={
                t: pd.DataFrame({"d": pd.to_datetime(days).date, "close": [100.0, 101.0]})
                for t in tickers
            },
        )
        monkeypatch.setattr(ingest.pd, "read_sql", dispatcher)

        def always_raises(ticker, start, end):  # noqa: ANN001, ANN201
            raise ingest.stooq.NotFoundError(ticker)

        monkeypatch.setattr(ingest.stooq, "fetch_daily", always_raises)

        result = ingest.run_validate(tickers=tickers, engine=_FakeEngine())

        assert result.clean is False
        assert result.stooq_checked == 0
        assert len(result.stooq_errors) == len(tickers)
        assert set(result.stooq_errors["ticker"]) == set(tickers)

    def test_successful_cross_check_with_no_disagreement_is_clean(self, monkeypatch):
        tickers = ["AAPL"]
        days = ["2020-01-02", "2020-01-03"]
        local = pd.DataFrame({"d": pd.to_datetime(days).date, "close": [100.0, 101.0]})
        stooq_frame = pd.DataFrame(
            {
                "ticker": ["AAPL", "AAPL"],
                "date": pd.to_datetime(days).date,
                "open": [100.0, 101.0],
                "high": [100.0, 101.0],
                "low": [100.0, 101.0],
                "close": [100.0, 101.0],
                "volume": [1000, 1000],
            }
        )
        dispatcher = _sql_dispatcher(
            reject_counts=EMPTY_REJECTS,
            coverage=_coverage(tickers, days[0], days[-1]),
            available=tickers,
            trading_days=_trading_days(days),
            bar_dates=_bar_dates([(t, d) for t in tickers for d in days]),
            local_by_ticker={"AAPL": local},
        )
        monkeypatch.setattr(ingest.pd, "read_sql", dispatcher)
        monkeypatch.setattr(ingest.stooq, "fetch_daily", lambda t, s, e: stooq_frame)

        result = ingest.run_validate(tickers=tickers, engine=_FakeEngine())

        assert result.clean is True
        assert result.stooq_checked == 1
        assert result.stooq_errors.empty
        assert result.stooq_disagreements.empty


class TestMissingBarWiring:
    def test_missing_bar_inside_a_tickers_span_makes_validation_not_clean(self, monkeypatch):
        tickers = ["AAPL"]
        days = ["2020-01-02", "2020-01-03", "2020-01-06"]
        # AAPL has bars for 01-02 and 01-06 but not 01-03, a trading day
        # inside its own span — a genuine gap, not pre-listing/post-delisting.
        bar_dates = _bar_dates([("AAPL", "2020-01-02"), ("AAPL", "2020-01-06")])
        local = pd.DataFrame(
            {
                "d": [pd.Timestamp("2020-01-02").date(), pd.Timestamp("2020-01-06").date()],
                "close": [100.0, 101.0],
            }
        )
        dispatcher = _sql_dispatcher(
            reject_counts=EMPTY_REJECTS,
            coverage=_coverage(tickers, days[0], days[-1]),
            available=tickers,
            trading_days=_trading_days(days),
            bar_dates=bar_dates,
            local_by_ticker={"AAPL": local},
        )
        monkeypatch.setattr(ingest.pd, "read_sql", dispatcher)
        monkeypatch.setattr(ingest.stooq, "fetch_daily", lambda t, s, e: pd.DataFrame())

        result = ingest.run_validate(tickers=tickers, engine=_FakeEngine())

        assert result.clean is False
        assert len(result.missing_bars) == 1
        assert result.missing_bars.iloc[0]["ticker"] == "AAPL"

    def test_no_bars_before_listing_or_after_delisting_is_clean(self, monkeypatch):
        # NYSE trading calendar spans wider than any single ticker's life;
        # a ticker's own pre-listing/post-delisting absence must not flag.
        calendar_days = ["2019-12-31", "2020-01-02", "2020-01-03", "2020-01-06", "2020-02-01"]
        tickers = ["NEWCO"]
        bar_dates = _bar_dates([("NEWCO", "2020-01-02"), ("NEWCO", "2020-01-03")])
        local = pd.DataFrame(
            {
                "d": [pd.Timestamp("2020-01-02").date(), pd.Timestamp("2020-01-03").date()],
                "close": [50.0, 51.0],
            }
        )
        dispatcher = _sql_dispatcher(
            reject_counts=EMPTY_REJECTS,
            coverage=_coverage(tickers, "2020-01-02", "2020-01-03"),
            available=tickers,
            trading_days=_trading_days(calendar_days),
            bar_dates=bar_dates,
            local_by_ticker={"NEWCO": local},
        )
        monkeypatch.setattr(ingest.pd, "read_sql", dispatcher)
        # Give the Stooq check something to agree on so this test isolates
        # the missing-bar logic — an empty/erroring Stooq response is its
        # own "check failed" case, covered by `TestStooqCheckVisibility`.
        stooq_frame = pd.DataFrame(
            {
                "ticker": ["NEWCO", "NEWCO"],
                "date": [pd.Timestamp("2020-01-02").date(), pd.Timestamp("2020-01-03").date()],
                "open": [50.0, 51.0],
                "high": [50.0, 51.0],
                "low": [50.0, 51.0],
                "close": [50.0, 51.0],
                "volume": [1000, 1000],
            }
        )
        monkeypatch.setattr(ingest.stooq, "fetch_daily", lambda t, s, e: stooq_frame)

        result = ingest.run_validate(tickers=tickers, engine=_FakeEngine())

        assert result.missing_bars.empty
        assert result.clean is True
