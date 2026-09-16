"""`nightly`'s `next_open` resolution step, and the two ways it wasted a night.

Built 2026-09-15 after the step ran nightly into `RuntimeMaxSec=4h` twice in
a row and neither attempt reached `sync`, so serving held no data for the day
while research held all of it (-> `OPERATIONS.md`).

The interesting properties are not "it returns tickers". They are that it
cannot select work the backtest it invokes is structurally incapable of
doing, that it is bounded no matter what the data looks like, and that no
ticker can starve.
"""

from __future__ import annotations

import re
from datetime import date

from capitalscan.jobs import cli


class _Conn:
    """Records the SQL and bound parameters, answers with fixed rows."""

    def __init__(self, rows=()):
        self.rows = rows
        self.params: dict = {}
        self.sql = ""

    def execute(self, stmt, params=None):
        self.sql = str(stmt)
        self.params = params or {}
        return list(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Engine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn


def _sql_for(rows=()):
    conn = _Conn(rows)
    cli._tickers_with_open_next_open(_Engine(conn), "abc123", 5, date(2026, 9, 15))
    return conn


class TestOutOfUniverseIsExcluded:
    """**The bug that cost the night.** 679 of the ~807 tickers holding an
    unresolved `next_open` position are in neither universe, and
    `candidates.apply_eligibility(include_out_of_universe=False)` -- which is
    what `nightly` and `weekly` both pass, deliberately (ADR 178) -- drops
    those rows. So the backtest this step invokes writes *zero* rows for
    them: measured on the 2026-09-15 run, 10,382 in-trade and 5,070 in-watch
    `next_open` rows written, and none at all out-of-universe.

    Selecting them is not merely wasteful, it is unbounded waste: the set
    only grows, because nothing in the scheduled chain can ever shrink it.
    """

    def test_query_requires_universe_membership(self):
        assert re.search(r"\(\s*in_trade\s+OR\s+in_watch\s*\)", _sql_for().sql, re.I)

    def test_query_still_requires_an_entered_unresolved_position(self):
        sql = _sql_for().sql
        assert "entry_date IS NOT NULL" in sql
        assert "exit_date IS NULL" in sql
        assert "'next_open'" in sql


class TestBounded:
    """A step that selects its own work needs its own cap, because the only
    thing downstream of it is a 4h kill that discards the whole chain rather
    than just this step."""

    def test_limit_is_bound_to_the_cap_constant(self):
        conn = _sql_for()
        assert conn.params["cap"] == cli._NEXT_OPEN_BACKTEST_TICKER_CAP
        assert "LIMIT :cap" in conn.sql

    def test_cap_stays_within_the_nightly_budget(self):
        """~16s/ticker measured 2026-09-15 (100 tickers in 26-27 min, twice).

        The cap times that rate is the step's worst case, and it has to leave
        room for the rest of the chain inside `RuntimeMaxSec=4h`.
        """
        assert cli._NEXT_OPEN_BACKTEST_TICKER_CAP * 16 <= 15 * 60

    def test_window_bound_comes_from_the_trading_calendar(self):
        """Not calendar arithmetic. `max_hold_days` counts sessions, and five
        sessions span seven calendar days over a weekend and more over a
        holiday, so a `CURRENT_DATE - n` bound means a different thing
        depending on when it runs. `trading_days` is the same calendar
        `_is_trading_day` and the Pi's poller guard read.
        """
        conn = _sql_for()
        assert "trading_days" in conn.sql
        assert conn.params["sessions"] == 5 + 1


class TestFutureSessionsCannotLeakIn:
    """`trading_days` is a **calendar**, not a log of what has happened: it
    held 74 dates beyond today on 2026-09-15, running to 2026-12-31. An
    unbounded `ORDER BY d DESC LIMIT n` therefore selects sessions that have
    not occurred, and the window bound silently stops bounding anything.

    `CURRENT_DATE` is not the fix either. The research database is
    `TimeZone = Etc/UTC` (CLAUDE.md, "Reading `runs`"), so during an evening
    Pacific run -- which is when `nightly` runs -- `CURRENT_DATE` is already
    tomorrow, and tomorrow's session counts as elapsed before it has opened.
    """

    def test_today_is_bound_as_a_parameter(self):
        conn = _sql_for()
        assert conn.params["today"] == date(2026, 9, 15)

    def test_query_excludes_sessions_after_today(self):
        assert re.search(r"d\s*<=\s*:today", _sql_for().sql, re.I)

    def test_today_is_not_read_from_the_clock(self):
        """ADR 060: the caller passes it, the same contract
        `candidates.apply_eligibility` holds for exactly this reason.

        The docstring is stripped before matching -- it names both
        `date.today()` and `CURRENT_DATE` to explain why neither is used, and
        a naive substring check reads its own explanation as a violation.
        """
        import ast
        import inspect
        import textwrap

        fn = ast.parse(textwrap.dedent(inspect.getsource(cli._tickers_with_open_next_open))).body[0]
        assert isinstance(fn, ast.FunctionDef)
        if ast.get_docstring(fn) is not None:
            fn.body = fn.body[1:]
        body = ast.unparse(fn)

        assert "date.today()" not in body
        assert "CURRENT_DATE" not in body


class TestNoStarvation:
    """`ORDER BY ticker LIMIT 40` always picks the same alphabetical prefix,
    so with more candidates than the cap a ticker late in the alphabet is
    never resolved -- it waits for the weekly instead, which is the staleness
    this step exists to remove."""

    def test_oldest_entry_is_selected_first(self):
        sql = _sql_for().sql
        assert re.search(r"ORDER BY\s+min\(entry_date\)\s+ASC", sql, re.I)

    def test_ordering_is_deterministic_on_a_tie(self):
        """ADR 060. Two tickers entered the same day must not swap places
        between runs, or the capped selection is nondeterministic."""
        assert re.search(r"ORDER BY\s+min\(entry_date\)\s+ASC\s*,\s*ticker", _sql_for().sql, re.I)


class TestReturnValue:
    def test_returns_the_tickers_the_query_produced(self):
        assert cli._tickers_with_open_next_open(
            _Engine(_Conn([("AAPL",), ("MSFT",)])), "abc123", 5, date(2026, 9, 15)
        ) == ["AAPL", "MSFT"]

    def test_empty_is_an_empty_list_not_none(self):
        """`nightly` branches on truthiness and skips the backtest entirely;
        `None` would raise on `len()` in the log line instead."""
        assert (
            cli._tickers_with_open_next_open(_Engine(_Conn([])), "abc123", 5, date(2026, 9, 15))
            == []
        )

    def test_config_hash_is_bound(self):
        assert _sql_for().params["chash"] == "abc123"
