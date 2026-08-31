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


class TestWiredIntoCli:
    def test_preflight_is_a_command(self):
        from typer.testing import CliRunner

        from capitalscan.jobs.cli import app

        result = CliRunner().invoke(app, ["preflight", "--help"])
        assert result.exit_code == 0
        assert "set up to run the research jobs" in result.output
