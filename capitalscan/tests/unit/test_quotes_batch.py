"""`fetch_quotes` must issue one request per batch, not one per ticker.

DESIGN §4.8 budgets the poller's quote step at "1-2 requests". The
original implementation called `yf.download(tickers, ...)`, which reads
as a batch call because it takes a list, but issues one HTTP request per
symbol internally — 140 requests every 300s against a 140-ticker
universe, ~10,900 per session.

Nothing caught it because `fetch_quotes` had no unit test: the only
coverage was `tests/integration/test_poll.py`, which asserts on rows
returned and never on requests made. The first test below is the
regression guard that closes that gap.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.jobs.fetch import yahoo

# Every test pins `now` rather than reading the clock, so the staleness
# rule below is exercised deterministically instead of depending on how
# long ago the fixture's epoch literal happens to be.
NOW = pd.Timestamp("2026-08-06 14:00:00", tz="UTC")
_FRESH_EPOCH = int((NOW - pd.Timedelta(seconds=30)).timestamp())


def _quote_payload(*symbols: str, price: float = 100.0, ts: int = _FRESH_EPOCH) -> dict:
    """A Yahoo `/v7/finance/quote` response body for `symbols`."""
    return {
        "quoteResponse": {
            "result": [
                {"symbol": s, "regularMarketPrice": price, "regularMarketTime": ts} for s in symbols
            ]
        }
    }


@pytest.fixture()
def calls(monkeypatch) -> list[list[str]]:
    """Records the symbol list handed to each HTTP request."""
    recorded: list[list[str]] = []

    def fake_download(symbols: list[str]) -> dict:
        recorded.append(list(symbols))
        return _quote_payload(*symbols)

    monkeypatch.setattr(yahoo, "_download_quotes", fake_download)
    return recorded


def test_full_universe_stays_inside_the_design_request_budget(calls):
    """The defect this module exists for.

    DESIGN §4.8 budgets the quote step at "1-2 requests" for the whole
    in_trade universe, which is 140 tickers. The old implementation sent
    140. The requirement is that request count tracks batches, not
    tickers, so this asserts the production-scale number directly rather
    than a bare "not 140".
    """
    tickers = [f"T{i}" for i in range(140)]

    out = yahoo.fetch_quotes(tickers, now=NOW)

    assert len(calls) <= 2, f"DESIGN §4.8 budgets 1-2 requests, got {len(calls)}"
    assert len(calls) == 2  # ceil(140 / QUOTE_BATCH_SIZE)
    # Every ticker asked for exactly once, across the batches.
    assert [t for batch in calls for t in batch] == tickers
    assert len(out) == 140


def test_returns_the_session_aggregates_per_symbol(calls):
    out = yahoo.fetch_quotes(["SPY", "GOOG"], now=NOW)

    # ADR 128 widened this: the same Yahoo response already carried the
    # session aggregates and the parser was dropping them.
    assert list(out.columns) == [
        "ticker",
        "ts",
        "price",
        "day_open",
        "day_high",
        "day_low",
        "day_volume",
    ]
    assert list(out["ticker"]) == ["SPY", "GOOG"]
    assert out["price"].tolist() == [100.0, 100.0]
    assert isinstance(out["ts"].iloc[0], pd.Timestamp)


def test_a_quote_without_an_open_keeps_the_row_and_nulls_the_open(monkeypatch):
    """ADR 108 needs `regularMarketOpen`, and the band signals do not.

    Dropping the quote over a missing field the breach path never reads
    would blind the poller to an ordinary band breach — a strictly worse
    failure than the reversal tag going unevaluated. So the row survives
    and `day_open` is NaN, which `poll.is_bear_reversal` treats as "cannot
    evaluate" rather than as a passing comparison.
    """

    def fake_download(symbols):
        return {
            "quoteResponse": {
                "result": [
                    {
                        "symbol": "SPY",
                        "regularMarketPrice": 769.79,
                        "regularMarketTime": _FRESH_EPOCH,
                    },  # no regularMarketOpen key
                ]
            }
        }

    monkeypatch.setattr(yahoo, "_download_quotes", fake_download)
    out = yahoo.fetch_quotes(["SPY"], now=NOW)

    assert list(out["ticker"]) == ["SPY"]
    assert out["price"].tolist() == [769.79]
    assert pd.isna(out["day_open"].iloc[0])


def test_the_open_is_carried_through_when_present(monkeypatch):
    def fake_download(symbols):
        return {
            "quoteResponse": {
                "result": [
                    {
                        "symbol": "SPY",
                        "regularMarketPrice": 769.79,
                        "regularMarketOpen": 775.10,
                        "regularMarketTime": _FRESH_EPOCH,
                    },
                ]
            }
        }

    monkeypatch.setattr(yahoo, "_download_quotes", fake_download)
    out = yahoo.fetch_quotes(["SPY"], now=NOW)

    assert out["day_open"].tolist() == [775.10]


def test_symbol_with_no_price_is_dropped_never_defaulted(monkeypatch):
    """Invariant 4: never fill a null. A quote without a price is not a
    zero and not the previous price — the row does not exist."""

    def fake_download(symbols):
        return {
            "quoteResponse": {
                "result": [
                    {
                        "symbol": "SPY",
                        "regularMarketPrice": 769.79,
                        "regularMarketTime": _FRESH_EPOCH,
                    },
                    {"symbol": "DEAD", "regularMarketTime": _FRESH_EPOCH},  # no price key
                    {
                        "symbol": "NULL",
                        "regularMarketPrice": None,
                        "regularMarketTime": _FRESH_EPOCH,
                    },
                ]
            }
        }

    monkeypatch.setattr(yahoo, "_download_quotes", fake_download)

    out = yahoo.fetch_quotes(["SPY", "DEAD", "NULL"], now=NOW)

    assert list(out["ticker"]) == ["SPY"]


def test_universe_larger_than_one_batch_splits(calls):
    tickers = [f"T{i}" for i in range(yahoo.QUOTE_BATCH_SIZE + 1)]

    yahoo.fetch_quotes(tickers, now=NOW)

    assert len(calls) == 2
    assert len(calls[0]) == yahoo.QUOTE_BATCH_SIZE
    assert len(calls[1]) == 1


def test_quote_older_than_the_max_age_is_dropped(monkeypatch):
    """`fetch_quotes`' own docstring: "a stale quote is a wrong signal".

    The previous implementation read the 1m chart, so a stale feed meant
    an empty frame and the poller fired nothing. The quote endpoint
    instead returns the last known price with its real timestamp, so on a
    day when Yahoo's feed lags (observed 2026-08-06: `regularMarketTime`
    pinned to the prior close while `marketState` read REGULAR) a naive
    port would compare yesterday's close against today's bands and fire.

    Dropping beats clamping here for the same reason as invariant 4: a
    price we cannot date is not a price.
    """
    now = pd.Timestamp("2026-08-06 14:00:00", tz="UTC")
    fresh = int((now - pd.Timedelta(seconds=30)).timestamp())
    stale = int((now - pd.Timedelta(hours=18)).timestamp())  # yesterday's close

    def fake_download(symbols):
        return {
            "quoteResponse": {
                "result": [
                    {"symbol": "FRESH", "regularMarketPrice": 100.0, "regularMarketTime": fresh},
                    {"symbol": "STALE", "regularMarketPrice": 200.0, "regularMarketTime": stale},
                ]
            }
        }

    monkeypatch.setattr(yahoo, "_download_quotes", fake_download)

    out = yahoo.fetch_quotes(["FRESH", "STALE"], now=now)

    assert list(out["ticker"]) == ["FRESH"]


def test_quote_with_no_timestamp_is_dropped(monkeypatch):
    """Undateable is indistinguishable from stale, so it gets the same
    treatment rather than being trusted by default."""

    def fake_download(symbols):
        return {"quoteResponse": {"result": [{"symbol": "NOTIME", "regularMarketPrice": 100.0}]}}

    monkeypatch.setattr(yahoo, "_download_quotes", fake_download)

    out = yahoo.fetch_quotes(["NOTIME"], now=pd.Timestamp("2026-08-06 14:00:00", tz="UTC"))

    assert out.empty


def test_no_tickers_makes_no_request(calls):
    out = yahoo.fetch_quotes([], now=NOW)

    assert calls == []
    assert out.empty
    # ADR 128 widened this: the same Yahoo response already carried the
    # session aggregates and the parser was dropping them.
    assert list(out.columns) == [
        "ticker",
        "ts",
        "price",
        "day_open",
        "day_high",
        "day_low",
        "day_volume",
    ]


def test_session_aggregates_are_absent_rather_than_defaulted(monkeypatch, calls):
    """Invariant 4, for the three columns ADR 128 added.

    Yahoo omits a field rather than sending null when a session has not
    produced it — pre-market, or a halted name. A volume of 0 and an unknown
    volume are different facts, and a partial candle drawn from a defaulted
    high would be a candle asserting a range that never happened.
    """
    payload = {
        "quoteResponse": {
            "result": [
                {
                    "symbol": "AAA",
                    "regularMarketPrice": 10.0,
                    "regularMarketTime": int(pd.Timestamp.now(tz="UTC").timestamp()),
                    # No high, low, volume, or open.
                }
            ]
        }
    }
    monkeypatch.setattr(yahoo, "_download_quotes", lambda symbols: payload)

    out = yahoo.fetch_quotes(["AAA"])

    assert out["price"].iloc[0] == 10.0
    for column in ("day_open", "day_high", "day_low", "day_volume"):
        assert pd.isna(out[column].iloc[0]), f"{column} was defaulted rather than absent"


def test_session_aggregates_are_read_when_present(monkeypatch, calls):
    """The other half — a quote carrying them must not lose them.

    `regularMarketDayHigh`/`Low` are cumulative session extremes rather
    than interval values, which is why one row per session can be
    overwritten each tick instead of accumulated.
    """
    payload = {
        "quoteResponse": {
            "result": [
                {
                    "symbol": "AAA",
                    "regularMarketPrice": 10.5,
                    "regularMarketTime": int(pd.Timestamp.now(tz="UTC").timestamp()),
                    "regularMarketOpen": 10.0,
                    "regularMarketDayHigh": 11.0,
                    "regularMarketDayLow": 9.5,
                    "regularMarketVolume": 1234567,
                }
            ]
        }
    }
    monkeypatch.setattr(yahoo, "_download_quotes", lambda symbols: payload)

    out = yahoo.fetch_quotes(["AAA"])

    assert out["day_open"].iloc[0] == 10.0
    assert out["day_high"].iloc[0] == 11.0
    assert out["day_low"].iloc[0] == 9.5
    assert out["day_volume"].iloc[0] == 1234567
    # The candle must be self-consistent: low <= close <= high.
    assert out["day_low"].iloc[0] <= out["price"].iloc[0] <= out["day_high"].iloc[0]
