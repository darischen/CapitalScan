# Migrating the serving layer to a Raspberry Pi

**Target:** Pi 4B, 4 GB RAM, 64 GB SD, LAN-only, reachable at
`capitalscan.local` (192.168.1.30). VS Code and Claude Code already
installed; assume nothing else is.

**Decided 2026-08-24.** LAN-only with mDNS, no port forwarding, Next.js on
the Pi so Postgres is never exposed. Tailscale later if remote access is
ever wanted — it keeps the LAN-only property and still opens no port.

---

## 0. What moves, and what does not

| | Where it lives after | Why |
|---|---|---|
| Research database (19 GB) | **Workstation, unchanged** | The jobs that write it run here. Moving it moves the compute. |
| All jobs — `nightly`, `backtest`, `universe`, poller | **Workstation, unchanged** | A 4 GB Pi cannot run a 1h15m 8-worker backtest, and it does not need to. |

**Settled 2026-08-24: the Pi serves and nothing else.** An earlier draft had
it running `nightly` and the poller against the workstation's research
database over the LAN. Dropped, because those jobs *write* to research — so
they need the workstation awake anyway, and the whole point was that the
site should survive the workstation being off.

The direction that falls out of this is the useful part: **`cscan sync` runs
on the workstation and connects out to the Pi**, so nothing ever needs to
reach the workstation's Postgres. It is now bound to `127.0.0.1` alone and
is unreachable from the network. One listener fewer, and the IPv6 exposure
question does not arise on that end at all.

The cost is the poller's coverage: it runs only while the workstation is up,
which is the status quo and what ADR 084 already records as accepted gaps.
| Serving database (~2 GB) | **Pi** | Derived, date-windowed, rebuilt by `sync` at will. |
| Next.js app | **Pi** | So the database connection is `localhost` and nothing is exposed. |
| Neon | **Retired** | Its 512 MB ceiling is the only reason `history_years` is 3. |

**You do not regenerate anything.** The serving store is derived. `cscan db
migrate` builds the schema, `cscan sync` fills it. If it is ever wrong, drop
it and sync again — that is the whole recovery procedure.

**The one real dependency:** the workstation must be able to reach the Pi's
Postgres on the LAN for `sync` to run. That is a private-network connection
between two machines you own, not an exposed port.

---

## 1. On the Pi — base system

**RDP first (done 2026-08-24).** `xrdp` needs only the network, so it goes
in before anything else and makes every later step easier to run:

```bash
sudo apt install -y xrdp avahi-daemon
sudo systemctl enable --now xrdp
sudo adduser xrdp ssl-cert
```

Connect from Windows with `mstsc` to `192.168.1.30:3389` — 3389 is RDP's
default, so the port is optional.

**`sudo` will warn "unable to resolve host capitalscan"** after
`hostnamectl set-hostname`, because `/etc/hosts` still maps the old name.
It is a warning and the command underneath still runs. Fix it so it stops:

```bash
sudo sed -i "s/127.0.1.1.*/127.0.1.1	capitalscan/" /etc/hosts
```

**Bookworm defaults to Wayland and xrdp expects X11.** A black screen or an
instant disconnect after login is that, not a credentials problem. Switch
with `sudo raspi-config` (Advanced Options → Wayland → X11) and reboot, or
log out of the physical console session — Pi OS will not give the same user
a console and an RDP session at once.

**Do not port-forward 3389.** Internet-exposed RDP is a far larger target
than the Postgres port this design already declines to expose.



```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y postgresql postgresql-contrib avahi-daemon git curl
```

`avahi-daemon` is what answers `capitalscan.local`. Confirm the hostname
first, because mDNS publishes *that*, not the project name:

```bash
sudo hostnamectl set-hostname capitalscan
sudo systemctl enable --now avahi-daemon
```

From the workstation, `ping capitalscan.local` should now answer. If it does
not, Windows needs Bonjour — it ships with iTunes, or install Apple's
Bonjour Print Services. **Check this before going further**; every later
step assumes the name resolves.

Node for the web app (Debian's `nodejs` is usually too old for Next.js):

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

---

## 1b. The workstation side, and a trap worth reading first

The Pi runs `nightly` and the poller **against the workstation's research
database**, so that database has to be reachable on the LAN. Three things
about this machine were not what the plan first assumed:

**It is PostgreSQL 16 in Docker**, not the native PG 18 on 15432. Config
files live inside the container, not under `C:\Program Files\PostgreSQL`.
Editing the native install's `pg_hba.conf` changes nothing — that was the
first wrong turn.

**`pg_hba.conf` cannot enforce LAN-only here, at all.** Docker NATs every
inbound connection through the bridge gateway, so a request from this host,
from the Pi, or from the internet all arrive as `172.18.0.1`. Postgres
cannot tell them apart by source address. Scoping the catch-all to
`192.168.1.0/24` locked out `localhost` and restricted nothing:

```
FATAL: no pg_hba.conf entry for host "172.18.0.1", user "capscan"
```

**The control belongs in the port publishing.** `ports: "5432:5432"`
publishes on `0.0.0.0` *and* `[::]`, and these machines hold globally
routable IPv6 addresses (`2600:6c50:...`). IPv6 has no NAT, so the only
barrier was the router's inbound firewall. `docker-compose.yml` now names
both interfaces:

```yaml
ports:
  - "127.0.0.1:5432:5432"
  - "192.168.1.14:5432:5432"
```

`netstat` afterwards shows two IPv4 listeners and no IPv6 entry. Loopback is
listed because every connection string in the project uses `localhost`.

**That last sentence was the bug, and it took a month to surface.** Removing
the IPv6 listener while every connection string says `localhost` means each
connect resolves `::1` first, finds nothing, and waits out a TCP timeout
before falling back.

Measured 2026-08-25: **`connect` 130.15s**, the query 0.00s, the fetch
0.04s. `_compute_one_ticker` opens a fresh connection per ticker
(`use_null_pool=True`), so every ticker paid 130 seconds. A 36-second
indicator run became hours, and it presented as a **hang** rather than as
slowness -- eight workers each sitting in a TCP timeout show 0% CPU and
open no database connections, which looks exactly like a deadlock.

Hours went into diagnosing it as a `ProcessPoolExecutor` deadlock, a wrong
interpreter, stale planner statistics, and a pickle-through-pipe problem.
It was none of them. `docker-compose.yml` now publishes **both loopback
families**:

```yaml
ports:
  - "127.0.0.1:5432:5432"
  - "[::1]:5432:5432"        # loopback only, never [::]
```

`localhost` went from 130.06s to 0.04s. `[::1]` rather than `[::]` keeps the
global-IPv6 exposure this section exists to prevent.

**The general lesson:** narrowing a listener is a change to every client
that names the host by a resolving alias, not only to the ones you were
thinking about.

**Recreating the container is safe** — the volume is named
`capitalscan-data`, so `docker compose up -d` preserves all 19 GB. Verified:
1,109,962 events before and after.

**The LAN address is DHCP.** Reserve a lease for `192.168.1.14`, or the
container fails to start the day it changes.

---

## 2. Postgres on the Pi — role, database, LAN listener

```bash
sudo -u postgres psql -c "CREATE ROLE capscan LOGIN PASSWORD 'CHOOSE_ONE';"
sudo -u postgres psql -c "CREATE DATABASE capitalscan_serving OWNER capscan;"
```

Postgres listens on localhost only by default. The **workstation** needs to
reach it to run `sync`, so open it to the LAN and nothing wider. In
`/etc/postgresql/*/main/postgresql.conf`:

```
listen_addresses = 'localhost,192.168.1.30'     # the Pi's own LAN address
```

In `pg_hba.conf`, add the workstation's subnet only:

```
host    capitalscan_serving    capscan    192.168.1.0/24    scram-sha-256
```

```bash
sudo systemctl restart postgresql
```

**That address does not exist yet at boot, and Postgres starts anyway.**
It warns on the address it cannot bind, binds the rest, and comes up on
loopback only -- so `systemctl` says `Started`, `pg_lsclusters` says
`online`, and every LAN client gets connection refused. The stock unit
orders on `network.target`, which means "networking is configured", not
"an address is assigned". ADR 152 has the full account; the fix is a
drop-in on the *cluster* unit, not on `postgresql.service`:

```bash
sudo systemctl edit postgresql@17-main
```

```ini
[Unit]
After=network-online.target
Wants=network-online.target
```

**Verify by rebooting, not by restarting.** A restart with the network
already up succeeds either way and proves nothing. The check is the
cluster's own log, since neither systemd nor `pg_lsclusters` records a
degraded start:

```bash
grep -c 'could not bind' /var/log/postgresql/postgresql-17-main.log
```

The count must not increase across a reboot. **The Pi reaches serving in
~39 seconds**; anything tested inside that window reads as an outage.

**Do not use `0.0.0.0` or `0.0.0.0/0` here.** The whole reason this design
is safe is that Postgres is reachable from the LAN and nowhere else. A
`/0` in `pg_hba.conf` plus any future router change is the failure this
avoids.

---

## 3. The repo and the toolchain

```bash
git clone <your remote> ~/CapitalScan
cd ~/CapitalScan
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

`uv sync` on a Pi builds some wheels from source; pandas and numpy have
aarch64 wheels, so this is minutes rather than hours.

**Only the serving env var is needed on the Pi.** It runs no jobs.

```
DATABASE_URL_SERVING=postgresql+psycopg://capscan:PW@localhost:5432/capitalscan_serving
```

---

## 4. Schema, then data

```bash
uv run cscan db status        # expect: no revision yet
uv run cscan db migrate       # builds every table and view
uv run cscan db status        # expect: c8d3a1f70b25 (head), or later
```

Then, **from the workstation**, point `DATABASE_URL_SERVING` at the Pi and
sync:

```
DATABASE_URL_SERVING=postgresql+psycopg://capscan:capscan@192.168.1.30:5432/capitalscan_serving
```

```

**IPv4, never `capitalscan.local`.** mDNS resolves that name to the Pi's
global **IPv6** address, and the Pi's `pg_hba.conf` line covers
`192.168.1.0/24` — IPv4 only. Using the hostname fails with `no pg_hba.conf
entry for host`, which reads as an auth problem rather than an
address-family mismatch.

```
uv run cscan sync --dry-run     # prints the cutoff and the 14 tables
uv run cscan sync
```

Expect ~1,010,000 rows and a few minutes over LAN. `sync` refuses to run
without `DATABASE_URL_SERVING` rather than falling back to research, so a
missed variable fails loudly instead of writing rows onto themselves.

**Verify with the catalogue, not the exit code:**

```sql
SELECT count(*) FROM events;
SELECT count(*) FILTER (WHERE in_watch) FROM v_universe;   -- 28 today
SET capitalscan.default_config_hash = 'f66729c7eda212a4';
SELECT count(*) FROM v_screen;
```

The GUC matters: `v_screen`, `v_watchlist` and `v_stats` all read
`current_setting('capitalscan.default_config_hash')`. `cscan db sync-config`
sets it where the role has permission; on Neon it could not (the role lacks
`ALTER DATABASE`) and the views fell back to the `serving_config` table per
ADR 115. On your own Pi the role owns the database, so this will work — set
it once:

```sql
ALTER DATABASE capitalscan_serving SET capitalscan.default_config_hash = 'f66729c7eda212a4';
```

---

## 5. The web app

```bash
cd ~/CapitalScan/web
npm ci
npm run build
```

`web/.env.local` needs the database URL and **auth on**:

```
DATABASE_URL_RESEARCH=postgresql://capscan:PW@localhost:5432/capitalscan_serving
SITE_PASSWORD=<choose one>
```

**Delete `SITE_AUTH_DISABLED` entirely.** Do not set it to `0` — the
middleware only opens on the exact string `"1"`, but leaving the key present
is a decision waiting to be flipped by accident. With `SITE_PASSWORD` unset
the site returns 503 rather than falling open, which is the correct failure
direction.

Run it as a service so it survives reboots:

```ini
# /etc/systemd/system/capitalscan-web.service
[Unit]
Description=CapitalScan web
After=network-online.target postgresql.service

[Service]
WorkingDirectory=/home/pi/CapitalScan/web
ExecStart=/usr/bin/npm run start
Restart=always
User=pi
Environment=PORT=3000

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now capitalscan-web
```

Then `http://capitalscan.local:3000`.

**Restart the service after every `npm run build`.** A build rewrites
`.next/` chunk hashes while a running server holds the old ones in memory,
so every asset 404s and the page renders as unstyled text. It looks exactly
like broken CSS and is not. This has bitten twice on the workstation.

---

## 6. Ongoing operation

The workstation keeps doing everything it does now. `cscan nightly` already
ends with a `sync` step — that step now writes to the Pi instead of Neon,
and it is wrapped so a Pi that is asleep reports a warning without failing
the ingest.

Nothing about the poller, the backtest, or the research database changes.

---

## Postgres tuning applied on the Pi (2026-08-26)

Both set live and verified. **Neither is in a migration** -- they are
properties of this machine, not of the schema, so a fresh serving store
built elsewhere will not have them and should.

```sql
-- as the postgres unix user: sudo -u postgres psql
ALTER DATABASE capitalscan_serving SET synchronous_commit = off;
ALTER SYSTEM SET max_wal_size = '4GB';
SELECT pg_reload_conf();
```

**`synchronous_commit = off`.** Every row in the serving store is derived
and rebuildable by `cscan sync`, so a crash losing the last few
transactions costs a re-sync and nothing else. That is exactly the trade
this setting is for, and on SD-card storage it is the single largest write
win available. Scoped to the database rather than the server, so anything
else on this Postgres keeps full durability.

**`max_wal_size` 1GB -> 4GB.** Its own log showed a checkpoint every ~270
seconds during a sync, each writing ~8,700 buffers over a ~400MB WAL
distance -- the 1GB ceiling was forcing checkpoints mid-transfer. 4GB lets
a whole sync land between them. 30GB free on the card, so the space is
there.

**Applies to new sessions.** `ALTER DATABASE ... SET` does not touch
connections that are already open, so a running job keeps the old value.

**Considered and not applied:** `wal_compression = on`, which trades CPU
for less WAL written. Plausible here -- the Pi sat at 24% CPU during a
sync -- but unmeasured, so it stays a suggestion rather than a change.

**Not doing:** moving the Pi to Ethernet. It runs on `wlan0` with power
save enabled (`eth0` has never carried a byte), and that is what killed a
53-minute sync on 2026-08-26. User's decision, 2026-08-26: the machine is
not going to be handled often, and the incremental sync cut the exposure
window from 114 minutes to ~2.

---

## What could bite

**mDNS is the single point of failure for the nice URL.** If
`capitalscan.local` stops resolving, the Pi is still fine — reach it by IP.
Worth reserving a DHCP lease for the Pi so its address does not move.

**The SD card is the weakest component.** The serving store is derived, so
the honest recovery for corruption is: reflash, re-run sections 1–5, sync.
That is an hour, not a disaster, and it is why nothing irreplaceable lives
here. Keep `pg_dump` out of the plan deliberately — a backup of derived data
invites treating it as a source.

**`history_years` can now grow.** Three years was Neon's 512 MB ceiling
talking; full history is 2,149 MB and the Pi has 64 GB. Widening it is a
`ServingParams` change plus a re-sync, and it closes the "serving store
grows without bound" backlog item by removing the bound that mattered.

**4 GB of RAM is fine for serving and not for jobs.** Postgres serving
indexed lookups over 2 GB is comfortable. Do not be tempted to move
`cscan backtest` here later; the harness alone spools ticker slices to
parquet and fans out across 8 workers.
