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

**`wivie` state, re-measured 2026-09-09.** Part A done, no data, all six
`capitalscan-{nightly,weekly,monthly}.{service,timer}` units present in
`/etc/systemd/system` carrying the ADR 160 changes (`Type=simple`,
`Restart=on-failure`, `OnBootSec`, the 19:00 nightly retry). **All three
timers are `disabled`** — enabling them is step 3, the point of cutover.
Nothing fires there until then.

**Corrected: the schema is NOT at head.** This section said it was, on
2026-09-01, and it was true then. `wivie` sits at `b7f3c5d21a94` against
head `a1c7f3b09d84` — **twelve migrations behind**, the whole prediction
chain (ADR 174 through 181).

**Do not fix that with `cscan db migrate`.** The `pg_restore` in step 2
carries the workstation's schema *and* data, both at head, so migrating
first spends twelve migrations on rows the restore is about to replace —
and leaves a window where `wivie` holds head's schema over an old
generation's data, which looks exactly like a working research database
and is not. The schema is not a separate task from the data.

**Sizing and duration, now measured end to end (2026-09-10).** The
2026-09-09 note here said research was 33 GB and to "expect the restore to
take hours rather than minutes". **Both halves were wrong**, and the
second was never measured -- it was inferred from the two cores.

| step | measured |
|---|---|
| `pg_dump -Fc -Z 6` (in the PG16 container) | **6m24s**, 26 GB -> **2.67 GB** archive |
| scp to `wivie` over Wi-Fi | **3m58s** |
| `pg_restore -j 2` | **13m26s**, exit 0, **zero errors** |
| `ANALYZE VERBOSE` | **7s** |
| **total** | **~24 min** |

Research is **26 GB**, not 33: the sweep cleanup took `path` from 11 GB to
4.7 GB. `events` is still 17 GB because it holds two generations.

**`ANALYZE` is seconds, not minutes, and that is not luck.** It samples
30,000 rows per table at the default statistics target, so it does not
scale with table size the way `VACUUM FULL` does. Do not budget for it as
if it did -- but do not skip it either: a PG16 dump carries no statistics
and `pg_restore` does not analyze, so the machine comes up with none.
-> `OPERATIONS.md`

The two cores do bound the restore, but `-j 2` and a 2.67 GB archive keep
it well inside a coffee break. Space was never the constraint: `wivie` has
383 GB free.

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
box or a `C:\Users\daris\...` path now describe the new one.

**The workstation is NOT wiped, and an earlier version of this line said it
could be.** This is a permanent two-machine arrangement: `wivie` takes the
*scheduled* role and the workstation stays the heavy-research box. Measured
2026-09-09, `wivie` is **3.41x** slower on this workload
(`scripts/cpu_bench.py`, steady 0.627 units/s against 2.138), so a full
`cscan backtest --workers 8` projects to **~6.8 h** there against ~2 h here,
on two physical cores and 7 GB of RAM. Arms and full backtests stay on the
workstation, which is what `CLAUDE.md` already says.

**Nothing on the Pi changes.** Its `DATABASE_URL_SERVING` is `localhost` —
it reads its own serving store and never touches research. What moves is
which machine *holds* research and *pushes* to it. `wivie`'s
`DATABASE_URL_SERVING` already points at `192.168.1.30`.

### After the restore, three things the dump does not carry

- **WAL and autovacuum tuning.** Server settings, not migrations, so they
  must be re-applied by hand. → `CLAUDE.md`
- **The `neural` extra.** Without it `nightly` skips `predict` visibly
  rather than failing. `uv sync --extra neural --extra dev` — the plain
  `--extra neural` prunes the dev group and breaks the integration tier.
  Budget ~37 min for `predict` there (11 min here × 3.41, projected not
  measured; torch's threading does not scale like the pandas hot path
  `cpu_bench` drives, so measure before quoting it).
- **`data/model/predictor.npz`** (ADR 181). Gitignored and stamped with the
  `config_hash` and `git_sha` it was fit under, so a copied one would be
  refused on any mismatch anyway. The first `nightly` on `wivie` refits and
  writes its own.

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

---

# Part D — Remote access via Tailscale

Set up 2026-09-09 so `wivie` (and, through it, the Pi) can be reached from
outside the LAN — a phone hotspot, a coffee shop, anywhere. Nothing here
changes what runs where; it only changes how you connect.

## D1. Topology

**`wivie` is the only machine running the Tailscale client. It is a subnet
router**, not a mesh of individually-joined devices. It advertises the
whole LAN, `192.168.1.0/24`, so any tailnet device reaches the workstation,
the Pi, and wivie itself by their normal `192.168.1.x` addresses — no
separate install needed on the Pi or the workstation for this to work.
wivie's own tailnet address (`100.65.212.123` as of 2026-09-09, `tailscale
status` on wivie for the current one) is a second way to reach wivie
specifically, useful for testing the tunnel itself apart from the subnet
route.

The client device (a laptop, this workstation, a phone) needs the
Tailscale app and must be logged into the **same tailnet account**
(`daris.chen@...`). **Check the account before trusting a connection** —
this workstation's first `tailscale up` silently signed into a stranger's
existing tailnet from a cached Windows credential, and `tailscale status`
showed a page of unfamiliar devices instead of an error. Logout and
re-`up` fixed it. There is no prompt that flags a wrong-tailnet login;
only the device list in `tailscale status` shows it.

## D2. Reaching things from outside the LAN

Once connected to the tailnet, addresses are the same ones used on the
LAN — the subnet route makes this transparent:

| What | Address | Notes |
|---|---|---|
| SSH to wivie | `ssh daris@192.168.1.12` | |
| SSH to the Pi | `ssh darischen@192.168.1.30` | user is `darischen`, not `daris` |
| Postgres on wivie | `psql -h 192.168.1.12 -p 5432 -U capscan -d capitalscan` | see D3 |
| CapitalScan web app | `http://192.168.1.30:3000` | served by the Pi, already listens on all interfaces |

`wivie`'s own tailnet IP (`100.65.212.123`) works as an alternative host
for the SSH and Postgres rows above — it bypasses the subnet route and
talks to wivie directly, which is a useful way to tell "is Tailscale
broken" apart from "is the subnet route not approved."

## D3. What had to change on wivie for this to work

- **Route approval.** Advertising a route (`tailscale up
  --advertise-routes=192.168.1.0/24`) is not enough by itself — it must
  also be approved for wivie in the tailnet admin console
  (Machines → wivie → Edit route settings). Unapproved, the route is
  invisible to other devices and there is no error, just no connection.
- **IP forwarding**, off by default on Debian, on in
  `/etc/sysctl.d/99-tailscale.conf` (`net.ipv4.ip_forward = 1`,
  `net.ipv6.conf.all.forwarding = 1`). Without it wivie can't route
  packets between the tailnet interface and the LAN, which is the whole
  job of a subnet router.
- **Postgres now listens on all interfaces** (`listen_addresses = '*'` in
  `/etc/postgresql/17/main/postgresql.conf`), not just localhost.
- **`pg_hba.conf` grants the tailnet CIDR access, scoped narrowly**: only
  the `capitalscan` database, only the `capscan` and `capscan_ro` roles,
  only `100.64.0.0/10` (Tailscale's whole address block, since a specific
  device's 100.x address isn't stable enough to pin). Not `host all all`
  — the tailnet reaches every current and future device on it, so the
  rule should reach only what's actually needed.
- Postgres was restarted (`sudo systemctl restart postgresql@17-main`) to
  pick up both config changes; check the actual instance unit, not the
  meta-unit `postgresql.service`, which reports `active (exited)` even
  when nothing changed.

No firewall change was needed on wivie — it runs neither `ufw` nor active
`nftables`/`iptables` rules, so nothing was gating the new listeners once
Postgres itself opened up. The Pi's web app needed no change at all; it
already listened on `*:3000`.

## D4. Client-side autolaunch (Windows)

The Windows Tailscale service installs as `Automatic` startup by default,
so `tailscaled` itself starts at boot without a login. That is not the
same as the *tunnel* surviving a logout — by default Windows drops the
tailnet connection when the GUI user session ends. Fix:

```
& "C:\Program Files\Tailscale\tailscale.exe" set --unattended
```

This keeps the machine reachable on the tailnet even before anyone logs
in, which matters for a machine you're trying to reach *because* you're
away from it.

## D5. Verifying from genuinely outside the LAN

A test run from a device still physically on `192.168.1.0/24` proves
nothing — normal LAN routing satisfies it even if Tailscale is broken.
Confirm the client's own address first (`ipconfig` / `ip a`; it should
show a `100.x` Tailscale adapter and a non-`192.168.1.x` local address),
then repeat the D2 table. All rows above were confirmed working from a
phone hotspot on 2026-09-09.
