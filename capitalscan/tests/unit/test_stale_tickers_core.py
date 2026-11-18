"""The stale-ticker report's pure logic (BACKLOG "Ten tickers stopped trading").

On 2026-11-08 ten acquired or delisted tickers had stayed `is_active = true`
for up to three months. The nightly now reports an active ticker with no
daily bar in more than N sessions, and checks first whether another active
ticker shares its CIK with current bars, which is ADR 207's symbol change.
"""

from __future__ import annotations

from datetime import date, timedelta

from capitalscan.core.config import DEFAULT_STALE_TICKERS, StaleTickerParams
from capitalscan.core.stale_tickers import (
    LastBar,
    StaleTicker,
    describe,
    find_stale,
    sessions_since,
    summarize,
)


def _weekdays(start: date, end: date) -> list[date]:
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


SESSIONS = _weekdays(date(2026, 10, 1), date(2026, 11, 17))
AS_OF = date(2026, 11, 17)


class TestDefaults:
    def test_ten_sessions_is_the_backlog_threshold(self) -> None:
        assert DEFAULT_STALE_TICKERS.stale_after_sessions == 10

    def test_it_is_not_a_config_field(self) -> None:
        """A reporting threshold must not move `config_hash`."""
        from dataclasses import fields

        from capitalscan.core.config import Config

        assert not any("stale" in f.name for f in fields(Config))


class TestSessionsSince:
    def test_a_bar_on_the_as_of_session_is_zero(self) -> None:
        assert sessions_since(AS_OF, SESSIONS, AS_OF) == 0

    def test_counts_sessions_not_calendar_days(self) -> None:
        """Friday to Tuesday is two sessions (Mon, Tue), four calendar days."""
        assert sessions_since(date(2026, 11, 13), SESSIONS, AS_OF) == 2

    def test_holidays_do_not_count(self) -> None:
        cal = [d for d in SESSIONS if d != date(2026, 11, 16)]
        assert sessions_since(date(2026, 11, 13), cal, AS_OF) == 1

    def test_sessions_after_as_of_are_ignored(self) -> None:
        cal = [*SESSIONS, date(2026, 11, 18), date(2026, 11, 19)]
        assert sessions_since(date(2026, 11, 13), cal, AS_OF) == 2

    def test_unsorted_calendar(self) -> None:
        assert sessions_since(date(2026, 11, 13), list(reversed(SESSIONS)), AS_OF) == 2

    def test_a_bar_on_a_non_session_day(self) -> None:
        """A Saturday bar is newer than Friday's session and older than Monday's."""
        assert sessions_since(date(2026, 11, 14), SESSIONS, AS_OF) == 2


def _n_sessions_ago(n: int) -> date:
    """The session `n` sessions before AS_OF."""
    return SESSIONS[-1 - n]


class TestFindStale:
    def test_exactly_the_threshold_is_not_stale(self) -> None:
        rows = [LastBar("AAA", "1", _n_sessions_ago(10))]
        assert find_stale(rows, SESSIONS, AS_OF, StaleTickerParams()) == []

    def test_one_past_the_threshold_is_stale(self) -> None:
        rows = [LastBar("AAA", "1", _n_sessions_ago(11))]
        (s,) = find_stale(rows, SESSIONS, AS_OF, StaleTickerParams())
        assert s.ticker == "AAA"
        assert s.sessions_since == 11
        assert s.last_bar == _n_sessions_ago(11)
        assert s.cik_peers == ()
        assert not s.possible_symbol_change

    def test_the_threshold_comes_from_params(self) -> None:
        rows = [LastBar("AAA", "1", _n_sessions_ago(3))]
        assert find_stale(rows, SESSIONS, AS_OF, StaleTickerParams(stale_after_sessions=2))

    def test_no_bar_at_all_is_reported(self) -> None:
        (s,) = find_stale([LastBar("NEW", None, None)], SESSIONS, AS_OF, StaleTickerParams())
        assert s.last_bar is None
        assert s.sessions_since is None

    def test_a_fresh_peer_on_the_same_cik_is_a_possible_symbol_change(self) -> None:
        """ADR 207: PSKY stopped, SKYD carries on under the same CIK."""
        rows = [
            LastBar("PSKY", "0000813828", _n_sessions_ago(20)),
            LastBar("SKYD", "813828", AS_OF),
            LastBar("OTHER", "999", AS_OF),
        ]
        (s,) = find_stale(rows, SESSIONS, AS_OF, StaleTickerParams())
        assert s.ticker == "PSKY"
        assert s.cik_peers == ("SKYD",)
        assert s.possible_symbol_change

    def test_a_stale_peer_is_not_a_symbol_change(self) -> None:
        """LBRDA and LBRDK share a CIK and stopped together: an acquisition."""
        rows = [
            LastBar("LBRDA", "1611983", _n_sessions_ago(30)),
            LastBar("LBRDK", "1611983", _n_sessions_ago(30)),
        ]
        out = find_stale(rows, SESSIONS, AS_OF, StaleTickerParams())
        assert [s.ticker for s in out] == ["LBRDA", "LBRDK"]
        assert all(s.cik_peers == () for s in out)

    def test_no_cik_has_no_peers(self) -> None:
        rows = [LastBar("QQQ", None, _n_sessions_ago(20)), LastBar("SPY", None, AS_OF)]
        (s,) = find_stale(rows, SESSIONS, AS_OF, StaleTickerParams())
        assert s.cik_peers == ()

    def test_blank_cik_is_no_cik(self) -> None:
        rows = [LastBar("A", " ", _n_sessions_ago(20)), LastBar("B", "", AS_OF)]
        (s,) = find_stale(rows, SESSIONS, AS_OF, StaleTickerParams())
        assert s.cik_peers == ()

    def test_symbol_changes_sort_first_then_oldest(self) -> None:
        rows = [
            LastBar("OLD", "1", _n_sessions_ago(30)),
            LastBar("MID", "2", _n_sessions_ago(15)),
            LastBar("RENAMED", "3", _n_sessions_ago(12)),
            LastBar("NEWSYM", "3", AS_OF),
        ]
        out = find_stale(rows, SESSIONS, AS_OF, StaleTickerParams())
        assert [s.ticker for s in out] == ["RENAMED", "OLD", "MID"]

    def test_input_is_not_mutated(self) -> None:
        rows = [LastBar("AAA", "1", _n_sessions_ago(11))]
        before = list(rows)
        find_stale(rows, SESSIONS, AS_OF, StaleTickerParams())
        assert rows == before


class TestText:
    def test_describe_names_the_peer(self) -> None:
        s = StaleTicker("PSKY", "813828", date(2026, 10, 16), 22, ("SKYD",))
        line = describe(s)
        assert "PSKY" in line
        assert "2026-10-16" in line
        assert "22 sessions" in line
        assert "SKYD" in line
        assert "symbol change" in line

    def test_describe_without_peer(self) -> None:
        line = describe(StaleTicker("EA", "712515", date(2026, 8, 4), 75, ()))
        assert "no active CIK peer" in line

    def test_describe_without_cik(self) -> None:
        line = describe(StaleTicker("QQQ", None, date(2026, 8, 4), 75, ()))
        assert "no CIK" in line

    def test_describe_without_bars(self) -> None:
        assert "no daily bar" in describe(StaleTicker("NEW", None, None, None, ()))

    def test_summarize_empty(self) -> None:
        assert summarize([], StaleTickerParams()) == "0 stale active ticker(s) (> 10 sessions)"

    def test_summarize_truncates(self) -> None:
        stale = [StaleTicker(f"T{i}", None, date(2026, 8, 4), 75, ()) for i in range(5)]
        notes = summarize(stale, StaleTickerParams(notes_limit=2))
        assert notes.startswith("5 stale active ticker(s) (> 10 sessions), 0 possible")
        assert "T0" in notes and "T1" in notes
        assert "T2" not in notes
        assert "+3 more" in notes

    def test_summarize_counts_symbol_changes(self) -> None:
        stale = [
            StaleTicker("PSKY", "1", date(2026, 10, 16), 22, ("SKYD",)),
            StaleTicker("EA", "2", date(2026, 8, 4), 75, ()),
        ]
        assert "1 possible symbol change(s)" in summarize(stale, StaleTickerParams())
