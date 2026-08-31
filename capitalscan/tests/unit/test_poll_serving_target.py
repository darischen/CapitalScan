"""The poller can write serving directly instead of research (ADR 158).

**The problem is scheduling, not performance.** `cscan poll` writes the
research store, so the workstation must be awake and free of conflicting
writers for a whole session. That one fact decides when a rebuild can run
and when the machine can be shut down.

**The research write is not load-bearing.** Every poller row is
provisional: `_sweep_provisional_poll_rows` deletes them on the next
nightly, which recomputes the authoritative version from bars. Research
receives rows, uses them as the source for the serving push, and drops them
hours later.

Two things must hold for the direct-to-serving path, and both are tested
here because both fail silently otherwise:

1. **No self-push.** `_push_live` copies research to serving. Writing
   serving and then pushing serving to serving is a no-op at best and a
   watermark corruption at worst.
2. **A staleness guard.** The poller reads `universe` and `indicators` from
   whichever store it targets. Serving is one sync behind, which is
   equivalent during a session and *wrong* after a missed sync — and silent
   either way, because a stale universe still returns rows.
"""

from __future__ import annotations

from datetime import date

import pytest

from capitalscan.jobs import poll as poll_job


class TestTheTargetIsAParameter:
    def test_run_poll_accepts_an_engine(self):
        """Already true; pinned so the direct-to-serving path cannot be
        removed by a refactor that re-hardcodes the research engine."""
        import inspect

        assert "engine" in inspect.signature(poll_job.run_poll).parameters

    def test_run_poll_accepts_push_live(self):
        """The switch that distinguishes the two modes."""
        import inspect

        params = inspect.signature(poll_job.run_poll).parameters
        assert "push_live" in params
        assert params["push_live"].default is True, (
            "the research-writing default must not change silently"
        )


class TestNoSelfPush:
    def test_the_push_is_guarded_by_push_live(self):
        import inspect

        src = inspect.getsource(poll_job.run_poll)
        assert "push_live" in src
        idx = src.index("_push_live(")
        window = src[max(0, idx - 400) : idx]
        assert "push_live" in window, (
            "_push_live is called unconditionally; writing serving and then "
            "pushing serving to serving corrupts the watermark"
        )


# A real slice of the US exchange calendar. 2026-08-22/23 and 08-29/30 are
# weekends; 2026-09-07 is Labor Day. Weekdays only, holiday dropped.
_CALENDAR = [
    date(2026, 8, 20),
    date(2026, 8, 21),
    date(2026, 8, 24),
    date(2026, 8, 25),
    date(2026, 8, 26),
    date(2026, 8, 27),
    date(2026, 8, 28),
    date(2026, 8, 31),
    date(2026, 9, 1),
    date(2026, 9, 2),
    date(2026, 9, 3),
    date(2026, 9, 4),
    date(2026, 9, 8),
]


class TestTheStalenessGuard:
    def test_it_exists(self):
        assert hasattr(poll_job, "assert_target_is_current")

    def test_a_current_watermark_passes(self):
        poll_job.assert_target_is_current(
            watermark=date(2026, 8, 27),
            last_trading_day=date(2026, 8, 27),
            trading_days=_CALENDAR,
        )

    def test_a_watermark_one_session_behind_passes(self):
        """Serving is synced by nightly *after* the session, so during a
        session it legitimately holds the previous session."""
        poll_job.assert_target_is_current(
            watermark=date(2026, 8, 26),
            last_trading_day=date(2026, 8, 27),
            trading_days=_CALENDAR,
        )

    def test_a_friday_watermark_on_monday_passes(self):
        """The 2026-08-31 false positive. Friday to Monday is three calendar
        days but one trading session, and the guard counts sessions."""
        poll_job.assert_target_is_current(
            watermark=date(2026, 8, 28),
            last_trading_day=date(2026, 8, 31),
            trading_days=_CALENDAR,
        )

    def test_a_friday_watermark_on_tuesday_raises(self):
        """Monday's sync really was missed: two sessions (08-31, 09-01)
        stand between Friday's data and Tuesday."""
        with pytest.raises(RuntimeError, match="stale"):
            poll_job.assert_target_is_current(
                watermark=date(2026, 8, 28),
                last_trading_day=date(2026, 9, 1),
                trading_days=_CALENDAR,
            )

    def test_a_watermark_across_a_holiday_weekend_passes(self):
        """Friday 09-04 to Tuesday 09-08 spans a weekend and Labor Day:
        four calendar days, one session."""
        poll_job.assert_target_is_current(
            watermark=date(2026, 9, 4),
            last_trading_day=date(2026, 9, 8),
            trading_days=_CALENDAR,
        )

    def test_an_older_watermark_raises(self):
        """A missed sync. The poller would otherwise resolve membership
        from a stale universe and never say so."""
        with pytest.raises(RuntimeError, match="stale"):
            poll_job.assert_target_is_current(
                watermark=date(2026, 8, 20),
                last_trading_day=date(2026, 8, 27),
                trading_days=_CALENDAR,
            )

    def test_a_missing_watermark_raises(self):
        """An empty or unreachable target is not 'probably fine'."""
        with pytest.raises(RuntimeError, match="stale|no watermark"):
            poll_job.assert_target_is_current(
                watermark=None, last_trading_day=date(2026, 8, 27), trading_days=[]
            )

    def test_the_message_names_both_dates(self):
        """An operator has to know how far behind it is to decide what to
        do about it."""
        with pytest.raises(RuntimeError) as exc:
            poll_job.assert_target_is_current(
                watermark=date(2026, 8, 20),
                last_trading_day=date(2026, 8, 27),
                trading_days=_CALENDAR,
            )
        assert "2026-08-20" in str(exc.value) and "2026-08-27" in str(exc.value)
