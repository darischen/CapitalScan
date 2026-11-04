"""The daily backup to the Pi (ADR 206).

Two things must agree with nothing at runtime to connect them: the
commands `jobs/backup.py` sends and the patterns the Pi's forced-command
gate accepts. A mismatch fails every night with "command not allowed" on
the Pi and nothing on `wivie` but a failed `runs` row. So the gate is read
here, from the copy in the repo, and every command is checked against it.
"""

from __future__ import annotations

import re
import subprocess
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from capitalscan.jobs import backup

GATE = Path(__file__).resolve().parents[3] / "scripts" / "pi" / "capitalscan-backup-gate"
WEDNESDAY = date(2026, 11, 4)
SUNDAY = date(2026, 11, 8)


def _gate_text() -> str:
    """The gate as the Pi runs it. A Windows checkout may carry CRLF."""
    return GATE.read_text(encoding="utf-8").replace("\r\n", "\n")


def _gate_patterns() -> set[str]:
    """Every literal the gate's `case` accepts, with `$dir` filled in."""
    text = _gate_text()
    directory = re.search(r"^dir=(\S+)$", text, re.M)
    assert directory, "the gate names no dir="
    patterns: set[str] = set()
    for line in text.splitlines():
        if re.match(r'^\s*"', line) and line.rstrip().endswith(")"):
            for literal in re.findall(r'"([^"]*)"', line):
                patterns.add(literal.replace("$dir", directory.group(1)))
    return patterns


class TestTheGate:
    def test_it_guards_the_folder_the_job_writes(self) -> None:
        assert f"dir={backup.REMOTE_DIR}\n" in _gate_text()

    @pytest.mark.parametrize("name", sorted(backup.gate_commands()))
    def test_it_accepts_every_command_the_job_sends(self, name: str) -> None:
        assert backup.gate_commands()[name] in _gate_patterns()

    def test_it_accepts_both_forms_of_the_scp_sink(self) -> None:
        """`scp -O` sends `scp -t <dir>/`, and newer clients add `--`."""
        sinks = {f"scp -t {backup.REMOTE_DIR}/", f"scp -t -- {backup.REMOTE_DIR}/"}
        assert sinks <= _gate_patterns()

    def test_it_accepts_nothing_else(self) -> None:
        sent = set(backup.gate_commands().values())
        sinks = {f"scp -t {backup.REMOTE_DIR}/", f"scp -t -- {backup.REMOTE_DIR}/"}
        assert _gate_patterns() == sent | sinks


class TestNames:
    def test_a_weekday_leaves_the_daily_pair(self) -> None:
        assert backup.remote_names(WEDNESDAY) == [
            "capitalscan-daily-2026-11-04.dump",
            "capitalscan-daily-2026-11-04-hourly.csv.gz",
        ]

    def test_a_sunday_leaves_a_weekly_pair_as_well(self) -> None:
        names = backup.remote_names(SUNDAY)
        assert names[2:] == [
            "capitalscan-weekly-2026-11-08.dump",
            "capitalscan-weekly-2026-11-08-hourly.csv.gz",
        ]

    def test_every_name_falls_under_a_prune_rule(self) -> None:
        """A file no `find` pattern matches would stay on the Pi forever."""
        prunes = [c for c in backup.gate_commands().values() if c.startswith("find ")]
        globs = [re.search(r"-name '([^']+)'", c).group(1) for c in prunes]  # type: ignore[union-attr]
        for name in backup.remote_names(SUNDAY):
            assert any(Path(name).match(g) for g in globs), name


def test_the_dump_holds_what_no_rerun_rebuilds() -> None:
    assert set(backup.TABLES) == {
        "predictions",
        "outcomes",
        "signal_reports",
        "poller_sessions",
        "runs",
        "positions",
        "order_intents",
    }


class _Calls:
    """Records each subprocess call and answers the listing with real sizes."""

    def __init__(self, sizes_off_by: int = 0) -> None:
        self.argv: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []
        self.uploaded: dict[str, int] = {}
        self.sizes_off_by = sizes_off_by

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.argv.append(argv)
        self.envs.append(kw.get("env"))
        if argv[0] == "pg_dump":
            Path(argv[argv.index("-f") + 1]).write_bytes(b"PGDMP" + b"x" * 100)
        if argv[0] == "scp":
            local = Path(argv[-2])
            self.uploaded[local.name] = local.stat().st_size + self.sizes_off_by
        out = ""
        if argv[0] == "ssh" and argv[-1] == backup.gate_commands()["list"]:
            out = "total 8\n" + "".join(
                f"-rw-r--r-- 1 1000 1000 {size} Nov  4 16:30 {name}\n"
                for name, size in self.uploaded.items()
            )
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """`run_backup` with every IO edge replaced: runs row, DB, subprocesses."""
    state: dict[str, Any] = {"report": SimpleNamespace(notes=None, rows_written=0)}

    @contextmanager
    def fake_run_job(engine: Any, job: str, params: dict) -> Any:
        state["job"] = job
        yield state["report"]

    def fake_hourly(engine: Any, path: Path) -> None:
        path.write_bytes(b"\x1f\x8b" + b"h" * 50)

    monkeypatch.setattr(backup.ingest, "run_job", fake_run_job)
    monkeypatch.setattr(backup, "_hourly", fake_hourly)
    engine = SimpleNamespace(
        url=SimpleNamespace(
            host="localhost",
            port=5432,
            username="capscan",
            password="s3cret-pw",
            database="capitalscan",
        )
    )
    state["engine"] = engine
    return state


class TestRun:
    def test_the_password_reaches_pg_dump_through_its_environment(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An argv is readable by every user on the box through `ps`."""
        calls = _Calls()
        monkeypatch.setattr(backup.subprocess, "run", calls)
        backup.run_backup(wired["engine"], today=WEDNESDAY)

        assert all("s3cret-pw" not in " ".join(a) for a in calls.argv)
        dump_env = calls.envs[[a[0] for a in calls.argv].index("pg_dump")]
        assert dump_env is not None and dump_env["PGPASSWORD"] == "s3cret-pw"

    def test_it_uploads_checks_then_prunes(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _Calls()
        monkeypatch.setattr(backup.subprocess, "run", calls)
        backup.run_backup(wired["engine"], today=WEDNESDAY)

        cmds = backup.gate_commands()
        sent = [a[-1] if a[0] == "ssh" else a[0] for a in calls.argv]
        assert sent == [
            "pg_dump",
            cmds["mkdir"],
            "scp",
            "scp",
            cmds["list"],
            cmds["prune_daily"],
            cmds["prune_weekly"],
        ]
        assert wired["job"] == "backup"
        assert wired["report"].rows_written == 2

    def test_a_sunday_uploads_four_files(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _Calls()
        monkeypatch.setattr(backup.subprocess, "run", calls)
        backup.run_backup(wired["engine"], today=SUNDAY)
        assert sorted(calls.uploaded) == sorted(backup.remote_names(SUNDAY))

    def test_a_short_upload_fails_before_anything_is_pruned(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pruning after a bad copy would delete good old copies for a bad new one."""
        calls = _Calls(sizes_off_by=-1)
        monkeypatch.setattr(backup.subprocess, "run", calls)
        with pytest.raises(RuntimeError, match="size"):
            backup.run_backup(wired["engine"], today=WEDNESDAY)
        assert not any(a[0] == "ssh" and a[-1].startswith("find ") for a in calls.argv)

    def test_every_remote_call_uses_the_backup_key_alone(
        self, wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without IdentitiesOnly, ssh offers the agent's keys first, and the
        Pi answers the first one it knows: Regicide's, whose gate refuses."""
        calls = _Calls()
        monkeypatch.setattr(backup.subprocess, "run", calls)
        backup.run_backup(wired["engine"], today=WEDNESDAY)
        for argv in calls.argv:
            if argv[0] in ("ssh", "scp"):
                assert str(backup.KEY_PATH) in argv
                assert "IdentitiesOnly=yes" in argv
                assert "BatchMode=yes" in argv
            if argv[0] == "scp":
                assert "-O" in argv
