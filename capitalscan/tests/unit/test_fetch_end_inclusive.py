"""`jobs/fetch/yahoo.py`'s date range is inclusive on both ends.

**The defect.** `yf.download`'s `end` is exclusive, and both download
helpers passed the caller's `end` straight through. Every other date range
in this codebase is inclusive — `run_events`, `run_indicators`,
`compute.scan`, `split_key_for` — so these two functions were the single
place that disagreed, and they disagreed silently.

**What it cost.** `cscan nightly` runs at 16:30 local, after the close, and
sets `end = date.today()`. It therefore requested bars through *yesterday*
and never ingested the session it had just run after. Measured on
2026-08-17 after a clean ten-phase nightly:

    max(bars.ts)            2026-08-14
    bars rows for 08-17     0
    max(indicators.ts)      2026-08-14
    max(events.signal_date) 2026-08-14

2026-08-17 was a full trading day in `trading_days`, not an early close.

**Why it survived.** Nothing failed. Every phase reported `ok`,
`bar_rejects` was empty, and `cscan scan --date <today>` printed "no events
found" — indistinguishable from *nothing fired today*, and the wrong
reading was the comfortable one.

These tests capture the `end` actually handed to yfinance, because that is
the only place the off-by-one is observable: no return value, row count, or
log line differs between the two behaviours.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from capitalscan.jobs.fetch import yahoo


@pytest.fixture
def captured(monkeypatch):
    seen: dict = {}

    def _fake_download(*args, **kwargs):
        seen.update(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(yahoo.yf, "download", _fake_download)
    return seen


START = date(2026, 8, 12)
END = date(2026, 8, 17)


def test_daily_requests_one_day_past_the_caller_s_end(captured):
    """The fix, stated as the caller's expectation: asking for data
    *through* 2026-08-17 must reach yfinance as `end=2026-08-18`."""
    yahoo._download_daily(["AAPL"], START, END)

    assert captured["end"] == END + timedelta(days=1), (
        "yfinance's end is exclusive; passing the caller's end verbatim "
        "silently drops the final day"
    )


def test_daily_leaves_start_alone(captured):
    """Only the exclusive bound moves. Shifting `start` too would quietly
    drop the first day instead."""
    yahoo._download_daily(["AAPL"], START, END)
    assert captured["start"] == START


def test_hourly_requests_one_day_past_the_caller_s_end(captured):
    yahoo._download_hourly("AAPL", START, END)
    assert captured["end"] == END + timedelta(days=1)


def test_hourly_leaves_start_alone(captured):
    yahoo._download_hourly("AAPL", START, END)
    assert captured["start"] == START


def test_a_single_day_range_is_not_empty(captured):
    """`start == end` must mean "just this day", not "nothing".

    Under the old behaviour this produced `start == end`, which yfinance
    treats as an empty range — so a one-day fetch returned nothing at all
    rather than one bar.
    """
    one_day = date(2026, 8, 17)
    yahoo._download_daily(["AAPL"], one_day, one_day)

    assert captured["start"] == one_day
    assert captured["end"] == one_day + timedelta(days=1)
    assert captured["end"] > captured["start"]


def test_daily_still_pins_auto_adjust_false(captured):
    """Guard on the guard. `auto_adjust=True` overwrites OHLC with adjusted
    values and destroys the split-adjusted series every band level is
    computed from — a far worse defect than the one above, and this file
    now touches the same call.
    """
    yahoo._download_daily(["AAPL"], START, END)
    assert captured["auto_adjust"] is False


def test_the_nightly_case_end_to_end(captured):
    """The exact shape that failed: `end = today`, run after the close.

    Named for the scenario rather than the mechanism, so a future reader
    grepping for why nightly missed a session lands here.
    """
    today = date(2026, 8, 17)
    yahoo._download_daily(["AAPL"], today - timedelta(days=5), today)

    assert captured["end"] > today, (
        "a nightly run on a trading day must actually request that day's bar"
    )
