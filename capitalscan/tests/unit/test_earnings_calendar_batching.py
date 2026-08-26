"""The forward earnings calendar is fetched in bulk, and truncation is caught.

**Two findings, 2026-08-26, and the second is the dangerous one.**

`run_earnings` called `fetch_forward_calendar(..., symbol=ticker)` once per
ticker. The endpoint is bulk -- omitting `symbol` returns every listing in
the range -- so 1,470 requests at `RATE_LIMIT_PER_SEC = 0.8` cost 30.6
minutes of pure rate limiting, and **43.5 minutes** measured end to end.

The obvious fix is a single bulk call, and it is wrong. **Finnhub truncates
at 1,500 entries and says nothing about it:**

    window        rows    tickers   AAPL present
    7 days         192        190       no
    30 days        483        476       no
    60 days      1,209      1,185       no
    90 days      1,500      1,496       no   <- exactly the cap
    Oct 26-30      905        905      YES   <- same data, smaller window

No `hasMore`, no error, no status code. The only tell is a row count
sitting on a round number. A naive swap would have looked 43 minutes
faster while silently dropping AAPL and most of the universe.

The chunked walk returned **5,149 rows across 4,945 tickers in 33 seconds**
over the same 90 days -- both faster than the loop and 3.4x more complete
than the single call.

No network here: `fetch_forward_calendar` is stubbed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from capitalscan.jobs.fetch import finnhub

START = date(2026, 9, 1)
COLUMNS = ["ticker", "date", "hour", "eps_estimate", "revenue_estimate", "source", "confidence"]


def _frame(n: int, prefix: str = "T") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": [f"{prefix}{i}" for i in range(n)],
            "date": ["2026-09-02"] * n,
            "hour": ["amc"] * n,
            "eps_estimate": [1.0] * n,
            "revenue_estimate": [1.0] * n,
            "source": ["finnhub"] * n,
            "confidence": ["estimate"] * n,
        },
        columns=COLUMNS,
    )


# ---------------------------------------------------------------------------
# Truncation, which is the correctness half
# ---------------------------------------------------------------------------


def test_a_response_at_the_cap_is_split(monkeypatch):
    """The property the whole design turns on. A full response is trusted;
    one sitting exactly on the limit is assumed clipped and halved."""
    seen: list[tuple[date, date]] = []

    def _fetch(start, end, symbol=None):
        seen.append((start, end))
        # only the undivided window is over the cap
        return _frame(finnhub.CALENDAR_PAGE_LIMIT if (end - start).days >= 4 else 10)

    monkeypatch.setattr(finnhub, "fetch_forward_calendar", _fetch)
    finnhub._fetch_calendar_range(START, START + timedelta(days=4))

    assert len(seen) > 1, "a capped response must be split, not returned"
    assert seen[0] == (START, START + timedelta(days=4))


def test_a_response_under_the_cap_is_not_split(monkeypatch):
    """Splitting a complete answer costs requests for nothing."""
    seen: list[tuple[date, date]] = []

    def _fetch(start, end, symbol=None):
        seen.append((start, end))
        return _frame(finnhub.CALENDAR_PAGE_LIMIT - 1)

    monkeypatch.setattr(finnhub, "fetch_forward_calendar", _fetch)
    finnhub._fetch_calendar_range(START, START + timedelta(days=4))
    assert len(seen) == 1


def test_a_single_day_at_the_cap_raises_rather_than_truncating(monkeypatch):
    """Unsplittable and knowably incomplete. A missing earnings date makes
    `days_to_earnings` wrong on every event near it, so invariant 4 applies:
    report it, never quietly return the clipped rows."""
    monkeypatch.setattr(
        finnhub,
        "fetch_forward_calendar",
        lambda s, e, symbol=None: _frame(finnhub.CALENDAR_PAGE_LIMIT),
    )
    with pytest.raises(finnhub.CalendarTruncated):
        finnhub._fetch_calendar_range(START, START)


def test_the_cap_matches_what_finnhub_actually_does():
    """Measured, not assumed: a 90-day window returned exactly 1,500."""
    assert finnhub.CALENDAR_PAGE_LIMIT == 1500


# ---------------------------------------------------------------------------
# The walk, which is the cost half
# ---------------------------------------------------------------------------


def test_the_window_is_covered_without_gaps_or_overlap(monkeypatch):
    """An off-by-one in the chunk stride silently loses a day of earnings."""
    seen: list[tuple[date, date]] = []

    def _fetch(start, end, symbol=None):
        seen.append((start, end))
        return _frame(1)

    monkeypatch.setattr(finnhub, "fetch_forward_calendar", _fetch)
    end = START + timedelta(days=20)
    finnhub.fetch_forward_calendar_many(START, end)

    covered: set[date] = set()
    for lo, hi in seen:
        d = lo
        while d <= hi:
            assert d not in covered, f"{d} requested twice"
            covered.add(d)
            d += timedelta(days=1)
    expected = {START + timedelta(days=i) for i in range((end - START).days + 1)}
    assert covered == expected


def test_it_costs_far_fewer_requests_than_one_per_ticker(monkeypatch):
    """The point of the change. 90 days must not approach 1,470 requests."""
    calls = 0

    def _fetch(start, end, symbol=None):
        nonlocal calls
        calls += 1
        return _frame(5)

    monkeypatch.setattr(finnhub, "fetch_forward_calendar", _fetch)
    finnhub.fetch_forward_calendar_many(START, START + timedelta(days=90))
    assert calls <= 25, f"{calls} requests for 90 days is not a bulk fetch"


def test_the_result_is_filtered_to_the_requested_universe(monkeypatch):
    """The calendar covers every US listing; the caller wants its own names."""
    monkeypatch.setattr(finnhub, "fetch_forward_calendar", lambda s, e, symbol=None: _frame(50))
    got = finnhub.fetch_forward_calendar_many(START, START + timedelta(days=4), ["T1", "T7"])
    assert set(got["ticker"]) == {"T1", "T7"}


def test_no_filter_returns_everything(monkeypatch):
    monkeypatch.setattr(finnhub, "fetch_forward_calendar", lambda s, e, symbol=None: _frame(50))
    got = finnhub.fetch_forward_calendar_many(START, START + timedelta(days=4))
    assert len(got) == 50


def test_duplicates_across_chunk_boundaries_are_collapsed(monkeypatch):
    """Adjacent chunks can both surface a company whose date sits on the
    boundary; `earnings` is keyed (ticker, report_date) and Postgres rejects
    an ON CONFLICT whose own rows collide."""
    monkeypatch.setattr(finnhub, "fetch_forward_calendar", lambda s, e, symbol=None: _frame(3))
    got = finnhub.fetch_forward_calendar_many(START, START + timedelta(days=20))
    assert len(got) == len(got.drop_duplicates(subset=["ticker", "date"]))


def test_an_empty_window_returns_the_empty_shape(monkeypatch):
    """Callers iterate the frame; a missing column would raise on a quiet
    window rather than doing nothing."""
    monkeypatch.setattr(
        finnhub,
        "fetch_forward_calendar",
        lambda s, e, symbol=None: pd.DataFrame(columns=COLUMNS),
    )
    got = finnhub.fetch_forward_calendar_many(START, START + timedelta(days=4))
    assert got.empty
    assert list(got.columns) == COLUMNS
