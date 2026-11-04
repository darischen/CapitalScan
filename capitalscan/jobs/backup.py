"""Copy what no rerun rebuilds to a folder on the Pi, once a day (ADR 206).

Everything else in research rebuilds from public data and code. These do
not: the forward log and the poller's records, which ADR 029 makes
permanent, and hourly bars, which Yahoo serves for 730 days and no longer.
Hourly bars go whole, not only the ones past the window, because a restore
can come weeks after the last copy, and any bar can age out by then.

**The key reaches one folder.** `wivie`'s CapitalScan key is authorized on
the Pi with a forced command, `scripts/pi/capitalscan-backup-gate`, that
runs fixed versions of the commands in `gate_commands` plus the `scp` sink,
and refuses anything else. The two must match exactly, with nothing at
runtime to connect them, so `test_backup.py` reads the gate and checks
every command. Regicide's backup key and gate are separate.

**Nothing reads the folder.** A restore is by hand (OPERATIONS.md). The
copy is pruned by age on the Pi: a week of dailies, four weeks of Sundays.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from capitalscan.jobs import db_io, ingest

#: The tables a rerun cannot rebuild. `order_intents` and `positions` are
#: records of what was emitted or entered at the time, like the forward log.
TABLES: tuple[str, ...] = (
    "predictions",
    "outcomes",
    "signal_reports",
    "poller_sessions",
    "runs",
    "positions",
    "order_intents",
)

HOURLY_SQL = (
    "COPY (SELECT * FROM bars WHERE interval = '1h' ORDER BY ticker, ts) "
    "TO STDOUT WITH (FORMAT csv, HEADER)"
)

PI_HOST = "darischen@192.168.1.30"
REMOTE_DIR = "/home/darischen/backups/capitalscan"
KEY_PATH = Path.home() / ".ssh" / "capitalscan_backup"

DUMP_SUFFIX = ".dump"
HOURLY_SUFFIX = "-hourly.csv.gz"
#: `find -mtime` ages; the gate holds the same numbers.
DAILY_KEEP_DAYS = 7
WEEKLY_KEEP_DAYS = 28
#: `date.weekday()`: the run that also writes a weekly copy.
WEEKLY_ON = 6

_SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=20")


def gate_commands() -> dict[str, str]:
    """Every command sent over ssh, exactly as the gate must accept it."""
    d = REMOTE_DIR
    return {
        "mkdir": f"mkdir -p '{d}' && chmod 700 '{d}'",
        "list": f"ls -ln '{d}'",
        "prune_daily": f"find '{d}' -name 'capitalscan-daily-*' -mtime +{DAILY_KEEP_DAYS} -delete",
        "prune_weekly": (
            f"find '{d}' -name 'capitalscan-weekly-*' -mtime +{WEEKLY_KEEP_DAYS} -delete"
        ),
    }


def remote_names(day: date) -> list[str]:
    """The files one run leaves on the Pi: the daily pair, and on Sundays a
    weekly pair beside it, so a Sunday copy outlives the daily window."""
    kinds = ["daily"] + (["weekly"] if day.weekday() == WEEKLY_ON else [])
    return [
        f"capitalscan-{kind}-{day:%Y-%m-%d}{suffix}"
        for kind in kinds
        for suffix in (DUMP_SUFFIX, HOURLY_SUFFIX)
    ]


def _pg_env(engine: Any) -> dict[str, str]:
    """libpq's variables for `pg_dump`. The password goes here, never in
    argv, where every user on the box can read it through `ps`."""
    url = engine.url
    env = dict(os.environ)
    env.update(
        {
            "PGHOST": str(url.host or "localhost"),
            "PGPORT": str(url.port or 5432),
            "PGUSER": str(url.username or ""),
            "PGDATABASE": str(url.database or ""),
        }
    )
    if url.password:
        env["PGPASSWORD"] = str(url.password)
    return env


def _dump(engine: Any, path: Path) -> None:
    argv = ["pg_dump", "--format=custom", "--compress=6"]
    for table in TABLES:
        argv += ["--table", table]
    argv += ["-f", str(path)]
    subprocess.run(argv, env=_pg_env(engine), check=True, capture_output=True, text=True)


def _hourly(engine: Any, path: Path) -> None:
    """Every hourly bar, as gzipped CSV, streamed rather than held."""
    with engine.connect() as conn:
        raw = conn.connection.driver_connection
        with raw.cursor() as cur, cur.copy(HOURLY_SQL) as copy, gzip.open(path, "wb") as out:
            for chunk in copy:
                out.write(chunk)


def _ssh(command: str) -> str:
    done = subprocess.run(
        ["ssh", "-i", str(KEY_PATH), *_SSH_OPTIONS, PI_HOST, command],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout


def _scp(local: Path) -> None:
    # `-O` is the legacy protocol, which runs `scp -t <dir>/` on the Pi:
    # the command the gate accepts. The default since OpenSSH 9 is SFTP,
    # which a forced command refuses.
    argv = ["scp", "-O", "-q", "-i", str(KEY_PATH), *_SSH_OPTIONS]
    subprocess.run(
        [*argv, str(local), f"{PI_HOST}:{REMOTE_DIR}/"],
        check=True,
        capture_output=True,
        text=True,
    )


def _sizes(listing: str) -> dict[str, int]:
    """`ls -ln` lines to {name: bytes}. The total line and blanks drop out."""
    sizes: dict[str, int] = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 9 and parts[4].isdigit():
            sizes[parts[-1]] = int(parts[4])
    return sizes


def run_backup(engine: Any = None, today: date | None = None) -> ingest.IngestReport:
    """Dump, copy to the Pi, check what arrived, then prune.

    **The check comes before the prune.** Pruning after a short copy would
    trade good old copies for a bad new one, so a size mismatch raises and
    leaves the Pi as it was. The `runs` row then reads 'failed', and the
    unit retries within the day.
    """
    engine = engine or db_io.get_engine()
    day = today or date.today()
    names = remote_names(day)
    cmds = gate_commands()
    params = {"day": str(day), "remote": f"{PI_HOST}:{REMOTE_DIR}", "tables": list(TABLES)}
    with ingest.run_job(engine, "backup", params) as report:
        with tempfile.TemporaryDirectory(prefix="capitalscan-backup-") as tmp:
            dump, hourly = Path(tmp) / names[0], Path(tmp) / names[1]
            _dump(engine, dump)
            _hourly(engine, hourly)
            files = [dump, hourly]
            for name, source in zip(names[2:], files, strict=False):
                copy = Path(tmp) / name
                shutil.copyfile(source, copy)
                files.append(copy)

            _ssh(cmds["mkdir"])
            for local in files:
                _scp(local)
            arrived = _sizes(_ssh(cmds["list"]))
            short = [
                f"{f.name} ({arrived.get(f.name)} of {f.stat().st_size} bytes)"
                for f in files
                if arrived.get(f.name) != f.stat().st_size
            ]
            if short:
                raise RuntimeError(f"upload size mismatch on the Pi: {', '.join(short)}")
            _ssh(cmds["prune_daily"])
            _ssh(cmds["prune_weekly"])

            report.rows_written = len(files)
            report.notes = (
                f"dump {dump.stat().st_size / 1e6:.1f} MB, hourly "
                f"{hourly.stat().st_size / 1e6:.1f} MB, {len(files)} file(s) to "
                f"{PI_HOST}:{REMOTE_DIR}, {len(arrived)} there before pruning"
            )
    return report
