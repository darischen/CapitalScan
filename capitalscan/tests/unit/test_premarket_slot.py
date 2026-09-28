"""The 05:30 PT `premarket` run: nightly's chain, pointed at the last closed session (ADR 203).

Yahoo's session bar right after the close often has an open outside its
own high/low, `open_outside_range` rejects it, and Yahoo corrects most of
them by early evening. A second nightly before the open recovers them
before the poller loads its bands at 06:45.
"""

from __future__ import annotations

import inspect
import re
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from capitalscan.jobs import cli, scheduled_runs

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parents[3]


class _Calendar:
    """An engine whose `trading_days` answers `prev` for every query."""

    def __init__(self, prev):
        self.prev = prev

    def connect(self):
        prev = self.prev

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                class _R:
                    def scalar_one_or_none(self):
                        return prev

                return _R()

        return _Conn()


class _Boom:
    def connect(self):
        raise RuntimeError("no database")


class TestNightlyEnd:
    def test_after_the_close_it_is_today(self):
        now = datetime(2026, 9, 28, 16, 15, tzinfo=ET)  # 13:15 PT
        assert cli._nightly_end(_Boom(), now) == date(2026, 9, 28)

    def test_before_the_open_it_is_the_previous_session(self):
        now = datetime(2026, 9, 29, 8, 30, tzinfo=ET)  # Tue 05:30 PT
        assert cli._nightly_end(_Calendar(date(2026, 9, 28)), now) == date(2026, 9, 28)

    def test_monday_premarket_means_friday(self):
        now = datetime(2026, 9, 28, 8, 30, tzinfo=ET)  # Mon 05:30 PT
        assert cli._nightly_end(_Calendar(date(2026, 9, 25)), now) == date(2026, 9, 25)

    def test_an_unreadable_calendar_falls_back_to_yesterday(self):
        now = datetime(2026, 9, 29, 8, 30, tzinfo=ET)
        assert cli._nightly_end(_Boom(), now) == date(2026, 9, 28)

    def test_an_empty_calendar_falls_back_to_yesterday(self):
        now = datetime(2026, 9, 29, 8, 30, tzinfo=ET)
        assert cli._nightly_end(_Calendar(None), now) == date(2026, 9, 28)

    def test_nightly_uses_it_rather_than_date_today(self):
        """`date.today()` is what swept today's poller rows pre-market."""
        src = inspect.getsource(cli.nightly)
        assert "end = _nightly_end(engine, _now_et())" in src
        assert "end = date.today()" not in src


class TestSlot:
    def test_premarket_is_a_daily_0530_slot(self):
        assert scheduled_runs.SCHEDULE["premarket"] == (time(5, 30), "daily")

    def test_the_1315_slot_is_unchanged(self):
        assert scheduled_runs.SCHEDULE["nightly"] == (time(13, 15), "daily")

    def test_nightly_records_and_completes_under_its_slot(self):
        src = inspect.getsource(cli.nightly)
        assert "scheduled_runs.record(engine, slot)" in src
        assert 'scheduled_runs.complete(engine, slot, "ok"' in src
        assert 'scheduled_runs.record(engine, "nightly")' not in src

    def test_premarket_resumes_on_its_own_period(self):
        """Yesterday's 13:15 run must not make a 05:30 run skip."""

        class _Engine:
            def begin(self):
                class _Conn:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                    def execute(self, sql, params):
                        assert params["job"] == "premarket"

                        class _R:
                            def first(self):
                                return None

                        return _R()

                return _Conn()

        decision, _ = scheduled_runs.resume_decision(
            _Engine(),
            "premarket",
            datetime(2026, 9, 29, 5, 30),
        )
        assert decision == "run"


class TestWrapper:
    def _sh(self) -> str:
        return (REPO / "scripts" / "run_job.sh").read_text()

    def test_premarket_shares_nightlys_lock(self):
        sh = self._sh()
        assert "LOCKJOB=nightly" in sh
        assert 'exec 9>"$LOGDIR/${LOCKJOB}.lock"' in sh

    def test_premarket_runs_the_nightly_chain_under_its_slot(self):
        assert "CMD=(nightly --slot premarket)" in self._sh()

    def test_premarket_never_starts_into_the_session(self):
        assert re.search(r"date \+%H%M\)\s*>=\s*630", self._sh())

    def test_ps1_mirrors_it(self):
        ps1 = (REPO / "scripts" / "run_job.ps1").read_text()
        assert "'premarket'" in ps1
        assert "$lockJob = 'nightly'" in ps1
        assert "@('nightly', '--slot', 'premarket')" in ps1


class TestTimer:
    def _timer(self) -> str:
        return (REPO / "scripts" / "systemd" / "capitalscan-premarket.timer").read_text()

    def _directives(self, text: str) -> list[str]:
        return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]

    def test_fires_weekdays_at_0530(self):
        assert "OnCalendar=Mon,Tue,Wed,Thu,Fri *-*-* 05:30:00" in self._directives(self._timer())

    def test_never_catches_up_into_the_session(self):
        d = self._directives(self._timer())
        assert not any(x.startswith(("Persistent=", "OnBootSec=")) for x in d)

    def test_service_does_not_retry_into_the_session(self):
        svc = (REPO / "scripts" / "systemd" / "capitalscan-premarket.service").read_text()
        d = self._directives(svc)
        assert not any(x.startswith("Restart=") for x in d)
        assert "ExecStart={{REPO}}/scripts/run_job.sh premarket" in d

    def test_install_script_installs_it(self):
        install = (REPO / "scripts" / "systemd" / "install.sh").read_text()
        assert "capitalscan-premarket" in install
