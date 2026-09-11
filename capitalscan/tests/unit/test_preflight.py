"""`cscan preflight` reports machine readiness (docs/SETUP.md).

The real check is that `cscan preflight` exits 0 on the research machine
and on the Pi -- that is an integration fact, not a unit one. These pin the
pieces a refactor could quietly break: the severity ordering, the exit
code mapping, and that the command is wired into the CLI.
"""

from __future__ import annotations

from capitalscan.jobs import preflight as pf


class TestSeverity:
    def test_worst_is_the_max_level(self):
        checks = [
            pf.Check("a", "ok", ""),
            pf.Check("b", "warn", ""),
            pf.Check("c", "ok", ""),
        ]
        assert pf.worst(checks) == "warn"

    def test_fail_beats_warn(self):
        checks = [pf.Check("a", "warn", ""), pf.Check("b", "fail", ""), pf.Check("c", "warn", "")]
        assert pf.worst(checks) == "fail"

    def test_all_ok(self):
        assert pf.worst([pf.Check("a", "ok", ""), pf.Check("b", "ok", "")]) == "ok"

    def test_empty_is_ok(self):
        assert pf.worst([]) == "ok"


class TestRequiredEnv:
    def test_the_four_ingest_keys_are_required(self):
        # Anything the nightly/weekly/sync path cannot run without. The
        # poller's NOTIFY_* and the web app's SITE_* are deliberately not
        # here -- a machine that only ingests should still pass.
        assert set(pf.REQUIRED_ENV) == {
            "DATABASE_URL_RESEARCH",
            "DATABASE_URL_SERVING",
            "SEC_USER_AGENT",
            "FINNHUB_API_KEY",
        }


class TestRole:
    def test_localhost_serving_url_means_this_is_the_serving_box(self, monkeypatch):
        monkeypatch.setenv(
            "DATABASE_URL_SERVING",
            "postgresql+psycopg://capscan:x@localhost:5432/capitalscan_serving",
        )
        monkeypatch.delenv("CAPSCAN_ROLE", raising=False)
        assert pf._role() == "serving"

    def test_remote_serving_url_means_research_machine(self, monkeypatch):
        monkeypatch.setenv(
            "DATABASE_URL_SERVING",
            "postgresql+psycopg://capscan:x@192.168.1.30:5432/capitalscan_serving",
        )
        monkeypatch.delenv("CAPSCAN_ROLE", raising=False)
        assert pf._role() == "research"

    def test_explicit_capscan_role_wins(self, monkeypatch):
        monkeypatch.setenv(
            "DATABASE_URL_SERVING",
            "postgresql+psycopg://capscan:x@localhost:5432/capitalscan_serving",
        )
        monkeypatch.setenv("CAPSCAN_ROLE", "research")
        assert pf._role() == "research"


class TestWiredIntoCli:
    def test_preflight_is_a_command(self):
        from typer.testing import CliRunner

        from capitalscan.jobs.cli import app

        result = CliRunner().invoke(app, ["preflight", "--help"])
        assert result.exit_code == 0
        assert "set up to run the research jobs" in result.output


class TestTheResearchTimezoneIsUtc:
    """**This check exists because `preflight` passed 8/8 on a machine that
    was about to corrupt every row it wrote.**

    `wivie`'s native PostgreSQL defaulted to `America/Los_Angeles` at the
    2026-09-10 cutover while every other store is UTC. Jobs write naive
    timestamps into `timestamptz`, so the server resolved them in the
    session zone and each trading day was inserted twice under different
    instants -- 2,930 bars and 5,662 indicators before the nightly died
    with a pandas error four frames from the cause.
    """

    def _tz(self, monkeypatch, value):
        """Stand in for the server's `SHOW TimeZone`, without a database."""
        import os

        from capitalscan.jobs import preflight as mod

        monkeypatch.setitem(os.environ, "DATABASE_URL_RESEARCH", "postgresql://x/y")

        class _Result:
            def scalar_one(self):
                if isinstance(value, Exception):
                    raise value
                return value

        class _Conn:
            def execute(self, *_a, **_k):
                return _Result()

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        class _Engine:
            def connect(self):
                return _Conn()

        monkeypatch.setattr(mod.__name__ + ".os", os, raising=False)
        from capitalscan.jobs import db_io

        monkeypatch.setattr(db_io, "get_engine", lambda *_a, **_k: _Engine())
        return mod._timezone_check()

    def test_utc_passes(self, monkeypatch):
        assert self._tz(monkeypatch, "UTC").level == "ok"

    def test_etc_utc_also_passes(self, monkeypatch):
        """The container reports `Etc/UTC` and a native install `UTC`.
        They are the same zone, so rejecting one would fail the workstation."""
        assert self._tz(monkeypatch, "Etc/UTC").level == "ok"

    def test_a_local_zone_is_a_hard_fail(self, monkeypatch):
        """`warn` would not be enough: the run completes and silently
        double-writes, which is worse than not running."""
        check = self._tz(monkeypatch, "America/Los_Angeles")
        assert check.level == "fail"
        assert "America/Los_Angeles" in check.detail

    def test_the_fix_names_the_commands(self, monkeypatch):
        check = self._tz(monkeypatch, "America/New_York")
        assert "ALTER SYSTEM" in check.fix and "UTC" in check.fix

    def test_an_unreadable_server_warns_rather_than_fails(self, monkeypatch):
        """A box with no database yet should not be told its timezone is
        wrong -- `research db` already reports that, and two FAILs for one
        cause sends the reader to the wrong fix."""
        assert self._tz(monkeypatch, RuntimeError("connection refused")).level == "warn"
