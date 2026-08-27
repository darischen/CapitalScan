"""Corporate actions are batched, and the cache key carries a date.

**The bug this closes, measured 2026-08-26.** `fetch_actions` was
`@cached(source="yahoo_actions", key_fn=lambda ticker: ticker)` -- keyed on
the bare ticker, with nothing saying *when*. The first fetch of a name
answered every later fetch of it, permanently.

Three cohorts, grouped by when each ticker entered the universe, and each
one's newest `ex_date` equalled its cache date exactly:

    cached        tickers   max ex_date    actions after
    2026-07-31      640     2026-07-31          0
    2026-08-21      314     2026-08-20          0
    2026-08-26      533     August present    118 tickers

118 of 533 fresh tickers (22%) had an August action, so ~141 of the 640
should have. They had none.

**Both halves must land together.** Dating the key alone makes every
nightly refetch 1,470 tickers one at a time -- ~49 minutes at
`RATE_LIMIT_PER_SEC = 0.5`. Batching alone stays fast and stays wrong.

No network here: the downloads are stubbed.
"""

from __future__ import annotations

import contextlib
import inspect
from datetime import date, timedelta

import pandas as pd

from capitalscan.jobs import ingest
from capitalscan.jobs.fetch import yahoo

START, END = date(2026, 8, 1), date(2026, 8, 26)


def _batch_frame(tickers: list[str]) -> pd.DataFrame:
    """Shaped like `yf.download(..., actions=True, group_by='ticker')`."""
    idx = pd.DatetimeIndex([pd.Timestamp("2026-08-11")])
    cols, data = [], {}
    for t in tickers:
        for field, value in (("Dividends", 0.5), ("Stock Splits", 0.0), ("Close", 10.0)):
            cols.append((t, field))
            data[(t, field)] = [value]
    return pd.DataFrame(data, index=idx, columns=pd.MultiIndex.from_tuples(cols))


# ---------------------------------------------------------------------------
# The cache key, which is the correctness half
# ---------------------------------------------------------------------------


def test_the_per_ticker_key_carries_the_date():
    """Two different days must be two different keys. This is the whole bug:
    without the date, 2026-07-31's answer is still served a month later."""
    key = yahoo._actions_key("AAPL")
    assert key.startswith("AAPL_")
    assert date.today().isoformat() in key


def test_the_source_string_moved_off_yahoo_actions():
    """Every `yahoo_actions` entry answers "the whole history as of whenever
    this file was written", which is not what the dated key asks. Reusing the
    source would let those entries keep answering it -- the failure CLAUDE.md
    records, where a correct fix merged, passed CI and never ran."""
    src = inspect.getsource(yahoo)
    assert 'source="yahoo_actions_v2"' in src
    assert 'source="yahoo_actions",' not in src


def test_the_batch_key_distinguishes_windows():
    assert yahoo._actions_batch_key(["A"], START, END) != yahoo._actions_batch_key(
        ["A"], START, END - timedelta(days=1)
    )


def test_the_batch_key_distinguishes_ticker_sets_and_ignores_order():
    assert yahoo._actions_batch_key(["A", "B"], START, END) != yahoo._actions_batch_key(
        ["A", "C"], START, END
    )
    assert yahoo._actions_batch_key(["B", "A"], START, END) == yahoo._actions_batch_key(
        ["A", "B"], START, END
    )


# ---------------------------------------------------------------------------
# The batching, which is the cost half
# ---------------------------------------------------------------------------


def test_one_request_covers_the_whole_batch(monkeypatch):
    calls: list[list[str]] = []

    def _dl(tickers, start, end):
        calls.append(list(tickers))
        return _batch_frame(list(tickers))

    monkeypatch.setattr(yahoo, "_download_actions_batch", _dl)
    yahoo._fetch_actions_window_batch.__wrapped__(["AAA", "BBB", "CCC"], START, END)
    assert len(calls) == 1
    assert calls[0] == ["AAA", "BBB", "CCC"]


def test_each_ticker_is_separated_out_of_the_batch(monkeypatch):
    monkeypatch.setattr(yahoo, "_download_actions_batch", lambda t, s, e: _batch_frame(list(t)))
    got = yahoo._fetch_actions_window_batch.__wrapped__(["AAA", "BBB"], START, END)
    assert set(got["ticker"]) == {"AAA", "BBB"}
    assert set(got["action_type"]) == {"dividend"}


def test_a_zero_valued_row_is_not_an_action(monkeypatch):
    """`yf.download` emits 0.0 for every day with no event. Recording those
    would turn one dividend into a row per trading day."""
    frame = _batch_frame(["AAA"])
    frame[("AAA", "Dividends")] = [0.0]
    monkeypatch.setattr(yahoo, "_download_actions_batch", lambda t, s, e: frame)
    got = yahoo._fetch_actions_window_batch.__wrapped__(["AAA"], START, END)
    assert got.empty


def test_a_ticker_absent_from_the_response_is_simply_absent(monkeypatch):
    """Unlike the bars paths there is no retry: "no actions in this window"
    and "not in the response" are the same true answer."""
    monkeypatch.setattr(yahoo, "_download_actions_batch", lambda t, s, e: _batch_frame(["AAA"]))
    got = yahoo.fetch_actions_many(["AAA", "BBB"], START, END)
    assert set(got) == {"AAA"}


# ---------------------------------------------------------------------------
# The two paths in run_actions
# ---------------------------------------------------------------------------


class _Report:
    run_id = "t"
    rows_written = 0
    tickers: list[str] = []


def _no_run_job(monkeypatch) -> None:
    @contextlib.contextmanager
    def _fake(engine, job, params):
        yield _Report()

    monkeypatch.setattr(ingest, "run_job", _fake)


def test_a_ticker_with_history_takes_the_batched_path(monkeypatch):
    """And must NOT cost a full-history request."""
    full: list[str] = []
    many: list[list[str]] = []

    def _full(t):
        full.append(t)
        return pd.DataFrame()

    def _many(ts, s, e):
        many.append(list(ts))
        return {}

    _no_run_job(monkeypatch)
    monkeypatch.setattr(ingest, "_tickers_with_actions", lambda e, t: set(t))
    monkeypatch.setattr(ingest.yahoo, "fetch_actions", _full)
    monkeypatch.setattr(ingest.yahoo, "fetch_actions_many", _many)
    monkeypatch.setattr(ingest.db_io, "upsert", lambda *a, **k: 0)

    ingest.run_actions(["AAA", "BBB"], engine=object())

    assert many == [["AAA", "BBB"]]
    assert full == [], "a known ticker must not pay for its whole history again"


def test_a_ticker_with_no_history_takes_the_full_path(monkeypatch):
    full: list[str] = []
    many: list[list[str]] = []

    def _full(t):
        full.append(t)
        return pd.DataFrame()

    def _many(ts, s, e):
        many.append(list(ts))
        return {}

    _no_run_job(monkeypatch)
    monkeypatch.setattr(ingest, "_tickers_with_actions", lambda e, t: {"AAA"})
    monkeypatch.setattr(ingest.yahoo, "fetch_actions", _full)
    monkeypatch.setattr(ingest.yahoo, "fetch_actions_many", _many)
    monkeypatch.setattr(ingest.db_io, "upsert", lambda *a, **k: 0)

    ingest.run_actions(["AAA", "NEW"], engine=object())

    assert many == [["AAA"]]
    assert full == ["NEW"]


def test_the_default_window_is_a_lookback_not_a_single_day(monkeypatch):
    """A one-day window loses anything a failed night missed. The upsert key
    is (ticker, ex_date, action_type), so an overlap is free -- which makes
    the window the cheap kind of insurance."""
    seen: list[tuple] = []

    def _many(ts, s, e):
        seen.append((s, e))
        return {}

    _no_run_job(monkeypatch)
    monkeypatch.setattr(ingest, "_tickers_with_actions", lambda e, t: set(t))
    monkeypatch.setattr(ingest.yahoo, "fetch_actions_many", _many)
    monkeypatch.setattr(ingest.db_io, "upsert", lambda *a, **k: 0)

    ingest.run_actions(["AAA"], engine=object())

    start, end = seen[0]
    assert (end - start).days == ingest.ACTIONS_WINDOW_DAYS
    assert ingest.ACTIONS_WINDOW_DAYS >= 7
