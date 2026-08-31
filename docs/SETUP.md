# Setting up the research machine

The research machine holds the research database and runs `nightly`,
`weekly`, `monthly`, and the manual `sync` to the Pi. The Pi is unchanged
and has its own runbook (`docs/PI_MIGRATION.md`).

This works on Windows or Linux. Where a step differs, both are given.

---

## 0. What you need

- Python 3.14 and [uv](https://docs.astral.sh/uv/)
- PostgreSQL 18, **native** (not Docker). The desktop ran Postgres in a
  container that did not restart after a reboot, which failed a nightly;
  native removes that whole class of problem.
- `psql` on `PATH` (or set `CAPSCAN_PSQL` to its full path)
- LAN reachability to the Pi's Postgres (`192.168.1.30:5432` by default)
- A dump of the research database from the outgoing machine (step 3)

---

## 1. Clone and install

```
git clone https://github.com/darischen/CapitalScan.git
cd CapitalScan
uv sync
```

`uv sync` creates `.venv/` with the locked dependencies. Every wrapper
script finds its interpreter and CLI there; nothing uses a global install.

---

## 2. PostgreSQL

Install PostgreSQL 18 natively.

**`capscan` must be a superuser here** (it is on the desktop, is not on the
Pi). `run_job` pins `capitalscan.default_config_hash` with `ALTER
DATABASE`, which needs superuser; without it the pin is silently skipped.

```
# as the postgres superuser
CREATE ROLE capscan LOGIN SUPERUSER PASSWORD 'capscan';
CREATE DATABASE capitalscan OWNER capscan;
```

`capitalscan_serving` lives on the Pi, not here.

**Bind both address families** if you ever expose this Postgres to the LAN
(so the Pi could be a fallback). An IPv4-only bind makes `localhost`
resolve to `::1`, wait for it to fail, and only then fall back -- ~2s per
connect, which multiplies across every backtest worker. In
`postgresql.conf`:

```
listen_addresses = 'localhost'          # or '*' plus a matching pg_hba rule
```

and if `'*'`, publish both in the service, never `0.0.0.0` alone.

---

## 3. Load the data

On the **outgoing** machine:

```
pg_dump -Fc -U capscan -d capitalscan -f capitalscan.dump
```

Copy `capitalscan.dump` over, then here:

```
pg_restore -U capscan -d capitalscan --no-owner --clean --if-exists capitalscan.dump
cscan db status          # confirm alembic head matches the repo
```

If `cscan db status` reports a revision behind the repo, run
`cscan db migrate --target research`.

---

## 4. Environment

```
cp .env.local.example .env.local
```

Fill in at least the first block: `DATABASE_URL_RESEARCH` (usually the
default `localhost` line), `DATABASE_URL_SERVING` (the Pi), `SEC_USER_AGENT`
(a real UA with contact info, or EDGAR 403s), `FINNHUB_API_KEY`. The rest
are for notifications, MCP, and the web app -- leave blank on this machine
if it does not run those.

---

## 5. Verify

```
cscan preflight
```

Checks `.env.local`, `psql`, both database connections, that config
resolves to the hash `serving_config` pins, the venv, and that the
schedule is installed. Exits non-zero with a fix for each failure. It
writes nothing.

Then a real dry run:

```
cscan sync           # ~30-45 min, pushes research -> serving
```

---

## 6. Install the schedule

### Windows

```
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
```

Registers `CapitalScan nightly` (daily 13:15), `CapitalScan weekly`
(Sunday 02:00), `CapitalScan monthly` (1st, 03:00) from the templates in
`scripts\tasks\`, substituting this repo's path. Idempotent -- re-run to
update. `-Remove` unregisters all three.

Verify / run now:

```
Get-ScheduledTask -TaskName 'CapitalScan *' | Select TaskName, State
Start-ScheduledTask -TaskName 'CapitalScan nightly'
```

### Linux

```
sudo scripts/systemd/install.sh
```

Fills `WorkingDirectory` and `User` into the unit templates in
`scripts/systemd/`, installs them to `/etc/systemd/system`, and enables
`capitalscan-nightly.timer`, `capitalscan-weekly.timer`,
`capitalscan-monthly.timer`. `--remove` reverses it.

Verify / run now:

```
systemctl list-timers 'capitalscan-*'
sudo systemctl start capitalscan-nightly.service
journalctl -fu capitalscan-nightly
```

---

## 7. Logs

Each run writes `reports/<job>/<job>_YYYY_MM_DD.log` (UTF-8), overwriting a
same-day rerun. `reports/nightly/` etc. are gitignored -- per-machine
artifacts.

- Windows: `(Get-ScheduledTaskInfo -TaskName 'CapitalScan nightly').LastTaskResult`
  -- `0` is success, anything else means read the log.
- Linux: `systemctl status capitalscan-nightly.service` and `journalctl -u
  capitalscan-nightly --since today`.

---

## Migration checklist (desktop -> laptop)

1. Desktop: `pg_dump -Fc` the research database (step 3).
2. Laptop: steps 1, 2, 3, 4.
3. Laptop: `cscan preflight` exits 0.
4. Laptop: `cscan sync` completes.
5. Laptop: install the schedule (step 6), `Start` / `systemctl start` nightly
   once, confirm it exits 0 and serving advances.
6. Desktop: `install_schedule.ps1 -Remove` so two machines do not both run
   nightly.
7. Nothing on the laptop depends on the desktop after this.
