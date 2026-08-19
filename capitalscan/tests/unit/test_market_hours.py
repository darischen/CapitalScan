"""`market_is_open()` and the poller agree on when a session is.

The bounds exist twice and cannot be shared: `poll.py::MARKET_OPEN` and
`MARKET_CLOSE` are Python `time` objects the poller compares against its own
clock, and `views.py::MARKET_IS_OPEN_DDL` is SQL a view evaluates. Neither
can import the other.

So the guarantee is this test. Without it the pair drifts silently, and the
failure it produces is the one that prompted the function: a price presented
as live by a view whose clock disagrees with the process that wrote it.
"""

from __future__ import annotations

import re

from capitalscan.jobs.poll import MARKET_CLOSE, MARKET_OPEN
from capitalscan.jobs.views import MARKET_IS_OPEN_DDL


def _sql_times() -> list[str]:
    """Every `TIME 'HH:MM'` literal in the function body, in order."""
    return re.findall(r"TIME '(\d{2}:\d{2})'", MARKET_IS_OPEN_DDL)


def test_the_sql_bounds_are_the_pollers_bounds():
    assert _sql_times() == [
        MARKET_OPEN.strftime("%H:%M"),
        MARKET_CLOSE.strftime("%H:%M"),
    ]


def test_the_function_reads_eastern_time():
    """Not `now()::time`, which is the server's zone.

    ADR 127 is the cautionary case: a naive read of an ET wall clock stored
    four hours off. This machine runs on Pacific, so a missing `AT TIME
    ZONE` here would open the session at 06:30 local and close it at 13:00
    -- plausible-looking numbers that are wrong by three hours.
    """
    assert MARKET_IS_OPEN_DDL.count("AT TIME ZONE 'America/New_York'") == 2


def test_the_close_is_exclusive_and_the_open_is_not():
    """16:00:00 exactly is closed; 09:30:00 exactly is open.

    The poller uses `MARKET_OPEN <= now <= MARKET_CLOSE`, inclusive at both
    ends, so it may write one tick at 16:00:00.000. The view is `>= open`
    and `< close`, which drops that tick from the live price and keeps it in
    the candle. That is the intended asymmetry: a bar may contain the
    closing print, and a price labelled live may not outlast the session.
    """
    assert ">=" in MARKET_IS_OPEN_DDL
    assert re.search(r"<\s+TIME '16:00'", MARKET_IS_OPEN_DDL)


def test_the_function_is_stable_not_volatile():
    """`STABLE` lets Postgres evaluate it once per statement.

    `VOLATILE` would re-evaluate per row, so a screener query spanning
    16:00:00 could show some rows a live price and others none, from one
    scan of one table.
    """
    assert "STABLE" in MARKET_IS_OPEN_DDL


def test_the_view_guards_the_live_price_with_it():
    """The function existing is not the point; the view using it is."""
    from capitalscan.jobs.views import V_SCREEN_LIVE_DDL

    assert "market_is_open()" in V_SCREEN_LIVE_DDL
    # The timestamp survives. A reader who wants to know when the last tick
    # landed still can; what goes away is the number pretending to be now.
    assert "lq.ts AS live_price_ts" in V_SCREEN_LIVE_DDL
