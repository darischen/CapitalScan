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
`capitalscan-nightly.timer` (13:15), `capitalscan-weekly.timer`
(Sun 02:00), `capitalscan-monthly.timer` (1st, 03:00).

```
systemctl list-timers 'capitalscan-*'
sudo systemctl start capitalscan-nightly.service     # run once now
journalctl -fu capitalscan-nightly                   # watch it
```

Confirm it exits 0 and the Pi's serving data advances. `cscan preflight`
should now show `schedule: OK`.

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

Do this in order. Nothing on the new machine depends on the old one
afterward.

1. **Old machine — dump research:**
   ```
   pg_dump -Fc -h localhost -U capscan -d capitalscan -f capitalscan.dump
   ```
   Copy `capitalscan.dump` to the new machine (scp, USB, whatever).

2. **New machine — do Part A (or B)** steps 1–8. Step A6 restores the
   dump.

3. **New machine — `cscan preflight` exits 0.**

4. **New machine — `cscan sync` completes.**

5. **New machine — install the schedule** (A9 / B8), run `nightly` once
   by hand, confirm exit 0 and that `serving` advances.

6. **Old machine — remove its schedule** so two machines do not both run
   nightly:
   - Windows: `powershell -File scripts\install_schedule.ps1 -Remove`
     (or delete "CapitalScan nightly" in Task Scheduler)

7. **Update `CLAUDE.md`** — the machine-specific notes that said
   "desktop" / a `C:\Users\daris\...` path now describe the new machine.

8. The old machine can be wiped.

---

# Reference

### The scheduled jobs

| job | when (PT) | does | ~time |
|---|---|---|---|
| `nightly` | daily 13:15 | `pull_live_records` from the Pi, ingest chain, indicators, events, `sync` to serving | 35-40 min |
| `weekly` | Sun 02:00 | `run_backtest` (no harness) | ~36 min |
| `monthly` | 1st, 03:00 | maintenance | short |

Deadlines are loose — `weekly` only has to land within ~2.5 days, and
every job self-heals on the next run (7-day lookback).

### Wrappers

- `scripts/run_job.{ps1,sh} <nightly|weekly|monthly>` — the real wrapper.
  Derives the repo root from its own location, uses `.venv`, runs a
  config-hash guard against `serving_config`, logs to
  `reports/<job>/<job>_YYYY_MM_DD.log`, propagates the exit code.
- `scripts/run_nightly.ps1` — a one-line shim to `run_job.ps1 nightly`,
  kept for older references.
- `scripts/install_schedule.ps1` / `scripts/systemd/install.sh` — install
  or `--remove` the schedule.

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
