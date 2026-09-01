# Setting up the research machine

This machine holds the **research** database and runs `nightly`, `weekly`,
`monthly`, and the `sync` that pushes results to the Pi. The Pi is
unchanged and has its own runbook (`docs/PI_MIGRATION.md`).

**Recommended OS: Debian** (headless). Reasons in `docs/BACKLOG.md` under
"The research machine is not portable". Windows works too and is Part B.

Follow the numbered steps. Each block is copy-paste. Where a value is
yours to choose it is written `<like-this>`.

---

## Before you start

- The research database is ~20 GB. Budget **60 GB free** for the restore
  plus room for rebuilds and WAL.
- The repo is private on GitHub. Have an SSH key or a personal access
  token ready for `git clone`.
- The scheduled times are **Pacific**. Set the machine's clock to
  `America/Los_Angeles` (step A2 / B2) or every job fires at the wrong
  hour.
- You need shell access to the **old** machine once, for the database
  dump (step C1).

---

# Part A — Debian

### A1. Packages

```
sudo apt update
sudo apt install -y git curl ca-certificates postgresql postgresql-client
```

### A2. Timezone

```
sudo timedatectl set-timezone America/Los_Angeles
timedatectl        # confirm
```

### A3. PostgreSQL role and database

`capscan` must be a **superuser** here (it is on the old desktop, is not
on the Pi). `run_job` pins `capitalscan.default_config_hash` with
`ALTER DATABASE`, which needs superuser; without it the pin is silently
skipped.

```
sudo -u postgres createuser --superuser --pwprompt capscan   # set password: capscan
sudo -u postgres createdb -O capscan capitalscan
```

Allow password login on localhost. Edit `pg_hba.conf` (path from
`sudo -u postgres psql -c 'SHOW hba_file'`), make the local IPv4/IPv6
lines use `scram-sha-256`:

```
host    all    all    127.0.0.1/32    scram-sha-256
host    all    all    ::1/128         scram-sha-256
```

```
sudo systemctl restart postgresql
PGPASSWORD=capscan psql -h localhost -U capscan -d capitalscan -c 'SELECT 1'   # must succeed
```

**Bind both address families only if** you later want the Pi to reach
this database as a fallback. `listen_addresses = '*'` in
`postgresql.conf` plus a `pg_hba.conf` rule for the LAN subnet. An
IPv4-only bind makes `localhost` resolve to `::1` first, wait, then fall
back — ~2s per connect, which multiplies across backtest workers.

### A4. uv

```
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env      # or restart the shell
uv --version
```

### A5. Clone and install

```
git clone git@github.com:darischen/CapitalScan.git ~/CapitalScan
cd ~/CapitalScan
uv sync          # creates .venv/ with the locked deps and Python 3.14
```

### A6. Load the data

Get `capitalscan.dump` from the old machine (step C1), then:

```
pg_restore -h localhost -U capscan -d capitalscan --no-owner --clean --if-exists capitalscan.dump
.venv/bin/cscan db status                       # note the revision
.venv/bin/cscan db migrate --target research    # only if it is behind the repo
```

### A7. Environment

```
cp .env.local.example .env.local
```

Edit `.env.local`. Fill the first block:

- `DATABASE_URL_RESEARCH` — the default `localhost` line is correct
- `DATABASE_URL_SERVING` — `postgresql+psycopg://capscan:<pw>@<pi-ip>:5432/capitalscan_serving`
- `SEC_USER_AGENT` — a real string with contact info, e.g.
  `"Your Name your@email"`; EDGAR 403s without it
- `FINNHUB_API_KEY` — from finnhub.io, free tier is enough

Leave the notification / MCP / web blocks blank on this machine.

### A8. Verify

```
.venv/bin/cscan preflight
```

Expect `role: research` and exit 0. `schedule` warns until step A9 —
that is fine. Any **FAIL** row: fix it (the row names the command) before
continuing.

Then a real dry run:

```
.venv/bin/cscan sync        # ~30-45 min, research -> serving
```

### A9. Install the schedule

```
sudo scripts/systemd/install.sh
```

Fills `User` and `WorkingDirectory` into the unit templates in
`scripts/systemd/`, installs them to `/etc/systemd/system`, and enables
`capitalscan-nightly.timer` (13:15 and a 19:00 retry),
`capitalscan-weekly.timer` (Sun 02:00), `capitalscan-monthly.timer`
(1st, 03:00).

```
systemctl list-timers 'capitalscan-*'
sudo systemctl start capitalscan-nightly.service     # run once now
journalctl -fu capitalscan-nightly                   # watch it
```

The nightly/weekly services are `Type=simple` (ADR 160), so
`systemctl start` **returns immediately** rather than blocking until the
job finishes — watch the run in `journalctl`, not by waiting on the
command. Confirm it exits 0 and the Pi's serving data advances.
`cscan preflight` should now show `schedule: OK`.

**Resume behaviour (ADR 160).** Each timer also fires `OnBootSec`, and the
nightly service retries on failure (`Restart=on-failure`, ~4 attempts over
an hour, then the 19:00 slot). `run_job.sh` calls `cscan resume-check`
first, so a boot or retry that finds the period's run already `ok` in
`scheduled_runs` logs `skip` and exits 0 without redoing it.

Uninstall: `sudo scripts/systemd/install.sh --remove`.

---

# Part B — Windows

### B1. Packages

- Git for Windows
- PostgreSQL 18 (native installer; **not** Docker). Put its `bin\` on
  `PATH`, or set `CAPSCAN_PSQL` to `...\PostgreSQL\18\bin\psql.exe`.
- uv: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

### B2. Timezone

Settings → Time & language → set to Pacific.

### B3. PostgreSQL role and database

In `psql` as the `postgres` superuser:

```
CREATE ROLE capscan LOGIN SUPERUSER PASSWORD 'capscan';
CREATE DATABASE capitalscan OWNER capscan;
```

### B4. Clone and install

```
git clone git@github.com:darischen/CapitalScan.git C:\CapitalScan
cd C:\CapitalScan
uv sync
```

### B5. Data — as A6, using the native `pg_restore`.

### B6. Environment — as A7 (`copy .env.local.example .env.local`).

### B7. Verify

```
.venv\Scripts\cscan preflight
.venv\Scripts\cscan sync
```

### B8. Install the schedule

```
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
```

Registers `CapitalScan nightly` / `weekly` / `monthly` from the templates
in `scripts\tasks\`, substituting this repo's path.

```
Get-ScheduledTask -TaskName 'CapitalScan *' | Select TaskName, State
Start-ScheduledTask -TaskName 'CapitalScan nightly'
```

**Task Scheduler caveat:** the tasks run as `InteractiveToken`, so they
only fire while a user is logged in. For an unattended box, either keep a
user logged in, or change each task to "Run whether user is logged on or
not" (needs the account password stored) — or use Debian.

Uninstall: `powershell -File scripts\install_schedule.ps1 -Remove`.

---

# Part C — Migrating from the old machine

Nothing on the new machine depends on the old one afterward. The Pi is
untouched throughout.

## C1. Cutover — when the new machine is already staged

"Staged" means Part A is done, `cscan preflight` exits 0, and the systemd
units are installed with their **timers disabled** (`systemctl is-enabled
capitalscan-nightly.timer` -> `disabled`).

**`wivie` state as of 2026-09-01.** Part A done, `cscan preflight` all-OK,
research schema at head but **no data** (the `pg_restore` in step 2 has not
run). The `capitalscan-{nightly,weekly}.{service,timer}` units in
`/etc/systemd/system` carry the ADR 160 changes (`Type=simple`,
`Restart=on-failure`, `OnBootSec`, the 19:00 nightly retry), rendered from
the repo and `daemon-reload`ed. **All three timers are `disabled`** —
enabling them is step 3 below, the point of cutover. Nothing fires on
`wivie` until then.

If that holds, the switch is four steps:

1. **Old machine — dump research** (during a market-closed window, no
   nightly running):
   ```
   pg_dump -Fc -U capscan -d capitalscan -f capitalscan.dump
   ```
   Copy it over (scp, USB).

2. **New machine — restore, then preflight:**
   ```
   pg_restore -U capscan -d capitalscan --clean --if-exists capitalscan.dump
   cscan preflight        # must exit 0; research schema at head, config hash matches serving
   ```

3. **New machine — go live** (this is the cutover: the timers were
   deliberately left `disabled` while staged):
   ```
   sudo systemctl enable --now capitalscan-nightly.timer \
        capitalscan-weekly.timer capitalscan-monthly.timer
   systemctl list-timers 'capitalscan-*'          # confirm NEXT times
   sudo systemctl start capitalscan-nightly.service   # one manual run
   journalctl -fu capitalscan-nightly                 # exit 0, serving advances
   ```
   `capitalscan-nightly.service` is `Type=simple` (ADR 160), so the manual
   `start` returns immediately — watch it in `journalctl`, not by waiting
   on the command. If `scripts/systemd/install.sh` was re-run at any point
   it also enables the timers, so on `wivie` skip it and enable by hand as
   above.

4. **Old machine — stand its schedule down** so two boxes never both run
   nightly:
   - Windows: `powershell -File scripts\install_schedule.ps1 -Remove`
   - Linux: `sudo scripts/systemd/install.sh --remove`

Then update `CLAUDE.md` — the machine-specific notes that named the old
box or a `C:\Users\daris\...` path now describe the new one. The old
machine can be wiped.

## C2. From scratch — new machine not yet staged

1. **Old machine — dump research** (as C1 step 1).
2. **New machine — do Part A (or B)** steps 1–9. Step A6 restores the
   dump instead of leaving the DB empty.
3. `cscan preflight` exits 0; `cscan sync` completes.
4. The schedule install in A9/B8 already enables the timers — so do the
   old machine's stand-down (C1 step 4) in the same sitting.
5. Update `CLAUDE.md`; wipe the old machine.

---

# Reference

### The scheduled jobs

| job | when (PT) | does | ~time |
|---|---|---|---|
| `nightly` | daily 13:15, 19:00 retry | `pull_live_records` from the Pi, ingest chain, indicators, events, `sync` to serving | 35-40 min |
| `weekly` | Sun 02:00 | `run_backtest` (no harness) | ~36 min |
| `monthly` | 1st, 03:00 | maintenance | short |

Deadlines are loose — `weekly` only has to land within ~2.5 days, and
every job self-heals on the next run (7-day lookback).

**Boot and failure resume (ADR 160).** Each timer also fires `OnBootSec`
(3 min nightly, 5 min weekly), covering a crash mid-run that
`Persistent=true` does not. The nightly/weekly services retry on failure
(`Restart=on-failure`, ~4 attempts over an hour) and the nightly timer has
a second 19:00 slot for one fresh full attempt. Every extra trigger is
gated by `cscan resume-check`, which exits 3 (wrapper logs `skip`, exits
0) when `scheduled_runs` already holds a `status='ok'` run for the current
period.

### Wrappers

- `scripts/run_job.{ps1,sh} <nightly|weekly|monthly>` — the real wrapper.
  Derives the repo root from its own location, uses `.venv`, takes an
  exclusive lock (a concurrent trigger exits 0), runs `cscan resume-check`
  then the config-hash guard against `serving_config`, logs to
  `reports/<job>/<job>_YYYY_MM_DD.log`, propagates the exit code.
- `scripts/run_nightly.ps1` — a one-line shim to `run_job.ps1 nightly`,
  kept for older references.
- `scripts/install_schedule.ps1` / `scripts/systemd/install.sh` — install
  or `--remove` the schedule.
- `cscan resume-check <job>` — read-only: exit 0 to run, 3 if this
  period's run already succeeded. Used by the wrappers; safe to run by
  hand to see what a trigger would decide.

### Health check

`cscan preflight` — run it any time. It checks `.env.local`, `psql`, both
database connections, the research schema against the repo's migration
head, that config resolves to the hash `serving_config` pins, and that
the schedule is installed. Read-only. `FAIL` exits 1; `warn` does not.
It infers `role` from `DATABASE_URL_SERVING` (localhost host = this *is*
the serving box), override with `CAPSCAN_ROLE=research|serving`.

### Logs

`reports/<job>/` (gitignored). Windows:
`(Get-ScheduledTaskInfo -TaskName 'CapitalScan nightly').LastTaskResult`
— `0` is success. Linux: `systemctl status capitalscan-nightly.service`,
`journalctl -u capitalscan-nightly --since today`.
