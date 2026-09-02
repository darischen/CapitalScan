# OPERATIONS

Incident postmortems and infrastructure traps. `CLAUDE.md` keeps the one-line
rule for each of these; the full story, the evidence, and the diagnostic
dead-ends live here.

Every entry is dated. A figure or a hash in an entry was true when written and
may not be now. Verify against the live system before acting on one.

Index:

- [A failed calendar query reads as a market holiday (Pi poller)](#a-failed-calendar-query-reads-as-a-market-holiday-pi-poller-2026-09-01)
- [The Pi is a separate clone, and pushing is not deploying](#the-pi-is-a-separate-clone-and-pushing-is-not-deploying)
- [Postgres: container down vs server moved](#postgres-container-down-vs-server-moved)
- [Container exited 255 unprompted](#container-exited-255-unprompted)
- [Binding the container IPv4-only makes `localhost` cost 2 seconds](#binding-the-container-ipv4-only-makes-localhost-cost-2-seconds)
- [A firewall rule scoped to Private/Domain dies when the profile flips to Public](#a-firewall-rule-scoped-to-privatedomain-dies-when-the-profile-flips-to-public)
- [`max_wal_size` left at the 1 GB default under heavy writes](#max_wal_size-left-at-the-1-gb-default-under-heavy-writes)
- [A table can go stale forever without anything reporting it](#a-table-can-go-stale-forever-without-anything-reporting-it)
- [`max_parallel_workers_per_gather` does not cover maintenance commands](#max_parallel_workers_per_gather-does-not-cover-maintenance-commands)
- [`psql` exits 0 on the shared-memory error](#psql-exits-0-on-the-shared-memory-error)
- [A catalogue sweep over `pg_attribute` must exclude dropped columns](#a-catalogue-sweep-over-pg_attribute-must-exclude-dropped-columns)
- [Sequences do not advance on an INSERT that supplies an explicit id](#sequences-do-not-advance-on-an-insert-that-supplies-an-explicit-id)
- [The fetch cache lies when you change what a key means](#the-fetch-cache-lies-when-you-change-what-a-key-means)
- [`2>&1` on a native exe is terminating under `$ErrorActionPreference = "Stop"`](#21-on-a-native-exe-is-terminating-under-erroractionpreference--stop)
- [`npm run build` invalidates a running `next start`](#npm-run-build-invalidates-a-running-next-start)
- [`cscan sync` picks its generation from the research GUC](#cscan-sync-picks-its-generation-from-the-research-guc)
- [A `psql` session on the Pi reads the wrong config generation](#a-psql-session-on-the-pi-reads-the-wrong-config-generation)
- [Every timestamp in `runs` is UTC](#every-timestamp-in-runs-is-utc)
- [`status = 'running'` is not evidence a job is running](#status--running-is-not-evidence-a-job-is-running)
- [Editing a module while a job runs gives it a split brain](#editing-a-module-while-a-job-runs-gives-it-a-split-brain)

---

## Postgres: container down vs server moved

**Postgres runs in Docker** as container `capitalscan-postgres`, mapped to
5432. A *separate* native PostgreSQL 18 service listens on **15432** and
rejects the `capscan` password. So `connection refused` on 5432 means the
container is down, not that the server moved -- check it before diagnosing
anything else:

```
"C:\Program Files\Docker\Docker\resources\bin\docker.exe" ps -a
"C:\Program Files\Docker\Docker\resources\bin\docker.exe" start capitalscan-postgres
```

`docker` is not on PATH in agent shells. Reach Postgres directly:

```
PGPASSWORD=capscan "/c/Program Files/PostgreSQL/18/bin/psql" -h localhost -U capscan -d capitalscan
```

Prefix `SET max_parallel_workers_per_gather=0;` if a **query** hits a shared-memory error:

```
could not resize shared memory segment ... No space left on device
```

## Container exited 255 unprompted

It exited 255 unprompted on 2026-08-21 at ~01:00 PT, killing a 1h55m
backtest. No OOM, no disk error in its log, nobody touched it. Crash
recovery lost nothing. Long jobs should be assumed interruptible.

This crash is also the leading suspect for [the stale-statistics
incident](#a-table-can-go-stale-forever-without-anything-reporting-it):
Postgres statistics are not crash-safe.

## Binding the container IPv4-only makes `localhost` cost 2 seconds

Caused on 2026-08-28 by rebinding the Postgres container to `0.0.0.0:5432`
so a second machine could reach it. The original bound **two** addresses:

```
127.0.0.1:5432->5432/tcp, [::1]:5432->5432/tcp
```

`0.0.0.0` is IPv4 only, so `localhost` resolves to `::1` first, waits for
that to fail, and only then falls back. Measured immediately after:

    localhost      2167 ms per connect
    127.0.0.1       120 ms
    192.168.1.31    117 ms

`DATABASE_URL_RESEARCH` uses `localhost`, and every backtest worker opens
its own connection, so **compute chunks went from 73-120s to 488-525s**, a
4-5x regression with no error and nothing in any log. It was diagnosed only
because `runs` held the before-and-after durations side by side.

Bind both families when exposing the container:

```
-p 0.0.0.0:5432:5432 -p "[::]:5432:5432"
```

The general rule: **any change to how the database is reached must be
followed by a connect-latency check**, because the symptom is uniformly
slower jobs rather than a failure.

```
for h in localhost 127.0.0.1; do ... psql -h $h -c "SELECT 1" ... done
```

Every timing taken between 2026-08-24 and 2026-08-26 was measured with this
bug live and is inflated. Distrust any duration recorded in that window.

## A firewall rule scoped to Private/Domain dies when the profile flips to Public

The symptom is a remote machine that looks broken. Exposing the research
database to the LAN on 2026-08-28 used:

```powershell
New-NetFirewallRule -DisplayName 'CapitalScan Postgres (LAN)' -Direction Inbound `
  -Protocol TCP -LocalPort 5432 -RemoteAddress 192.168.1.0/24 -Profile Private,Domain
```

The workstation is on Wi-Fi, and its profile was **Public**. The rule never
applied, so 5432 was blocked from the LAN while `localhost` kept working
perfectly -- meaning **every local job was unaffected and only the second
machine failed**, which is exactly the shape that makes you blame the second
machine. It failed three different ways over four hours:
`server closed the connection`, then a runner alive with zero output for 45
minutes, then `ConnectionTimeout: connection timeout expired`.

Fix the network category rather than widening the rule:

```powershell
Set-NetConnectionProfile -InterfaceAlias 'Wi-Fi 3' -NetworkCategory Private
```

`-Profile Any` also works and is wrong: it opens 5432 on every network the
machine ever joins, including public Wi-Fi.

**Check this first when a remote writer fails and the local one is fine:**

```powershell
Get-NetConnectionProfile | Select InterfaceAlias, NetworkCategory
```

**Three explanations were reached for before the right one** -- the laptop's
Wi-Fi, then the database's WAL settings, then the firewall. Only the third was
the cause. The first two were guesses that fit the symptom; the third was
found by checking configuration rather than by reasoning about behaviour.

## `max_wal_size` left at the 1 GB default under heavy writes

**The research database ran on the default `max_wal_size` until 2026-08-29,
and the Pi was better tuned than the workstation.** Under the exit sweep's
write load its own log read:

```
LOG:  checkpoints are occurring too frequently (9 seconds apart)
HINT: Consider increasing the configuration parameter "max_wal_size".
```

Checkpoints every 9-28 seconds, each flushing 48k-89k buffers and taking 6-27
seconds -- near-continuous. The setting was **1 GB, the stock default**, on the
store that takes the heaviest writes in the system (2M events and 6.5M path
rows per backtest arm), while `capitalscan_serving` on the Pi had been given
4 GB deliberately.

Raised to 4 GB. **`max_wal_size` is `sighup`**, so it applies without a
restart and without dropping a connection, which matters when a multi-hour
job is running:

```
psql -c "ALTER SYSTEM SET max_wal_size = '4GB'"   # its own invocation:
psql -c "SELECT pg_reload_conf()"                 # ALTER SYSTEM cannot run
                                                  # inside a transaction, and
                                                  # psql wraps multiple -c in one
```

`synchronous_commit` was left `on`. The Pi can afford `off` because serving is
a derived copy; research is the source of truth and losing the last few
transactions to a crash is a different trade.

**The diagnostic lesson is the bigger one.** A second machine writing over the
network failed with `OperationalError: server closed the connection`, and the
first explanation reached for was its Wi-Fi. The server's own log said
otherwise. A client-side network symptom is not evidence of a client-side
cause: the local writer rides out a stalled checkpoint that a remote one
cannot. **Read `docker logs capitalscan-postgres` before blaming the link.**

## A table can go stale forever without anything reporting it

Found 2026-08-25: `indicators` held **4,011,351 rows while the planner believed
80,043** -- a 50x underestimate on every plan touching it. The symptom was a
job that had "worked forever" suddenly crawling: 20 tickers of full history
took **36 seconds on 2026-08-21** and 3 tickers took **8.8 minutes on
2026-08-25**, with no change to the code path. `git log` on `compute.py`
showed nothing touching `run_indicators`, which is what sent the
investigation to the database.

    relname      n_live_tup   actual      last_autoanalyze
    indicators       80,043   4,011,351   never
    bars          8,152,486   8,154,808   2026-08-26
    events       16,175,934   16,269,169  2026-08-25

**Autovacuum was on, with no table-level override.** One table was simply
never serviced. The likely cause, assembled from three facts rather than
observed: Postgres statistics are **not crash-safe**, the container exited
255 unprompted on 2026-08-21, and autoanalyze fires at
`50 + 0.1 * n_live_tup` -- about 8,054 at the stale estimate. The only
successful indicator writes since that crash were two nightly runs of 3,561
and 4,497 rows, **8,058 total**, sitting on the threshold.

**The trap is that the trigger scales with the number that is wrong.** A
table growing in large batches, whose counters were reset, can sit under its
own threshold indefinitely.

Fixed by making the trigger absolute on the two tables that grow in bulk:

```sql
ALTER TABLE indicators SET (
  autovacuum_analyze_scale_factor = 0.0,
  autovacuum_analyze_threshold    = 50000,
  autovacuum_vacuum_scale_factor  = 0.0,
  autovacuum_vacuum_threshold     = 50000
);
```

**Run `VACUUM (PARALLEL 0, ANALYZE)` after any bulk write**, and check
`n_live_tup` against a real `count(*)` when a job is inexplicably slow.
Six seconds of maintenance was the whole fix here, after roughly four hours
lost to diagnosing it as a hang, a deadlock, a wrong interpreter and a
`ProcessPoolExecutor` bug -- none of which it was.

## `max_parallel_workers_per_gather` does not cover maintenance commands

It governs query-time gather nodes only. `VACUUM` parallelises index cleanup
under `max_parallel_maintenance_workers` and ignores it entirely, so a large
vacuum fails with the same shared-memory message and setting
`max_parallel_workers_per_gather=0` does nothing. Use the command's own
option:

```
VACUUM (PARALLEL 0, ANALYZE) tablename;
```

`REINDEX` and `CREATE INDEX` take `max_parallel_maintenance_workers` too.

## `psql` exits 0 on the shared-memory error

It goes to stderr, so a piped or filtered invocation swallows it and the
command looks like it worked. On 2026-08-17 two `VACUUM` runs on `path`
failed this way and were only caught because `pg_stat_user_tables.last_vacuum`
was still NULL with 57.3M dead tuples. Verify maintenance with the catalog,
never with the exit code:

```sql
SELECT relname, n_dead_tup, last_vacuum, last_analyze
FROM pg_stat_user_tables WHERE relname = 'path';
```

## A catalogue sweep over `pg_attribute` must exclude dropped columns

`DROP COLUMN` does not delete the row -- it renames the column to a
`........pg.dropped.N........` placeholder and sets `attisdropped`, so the
physical tuple layout is preserved. Worse, `pg_get_serial_sequence` **raises**
on a name that is not a real column instead of returning NULL, so a
`WHERE ... IS NOT NULL` filter meant to skip it never runs and the whole
statement aborts:

```
ERROR: column "........pg.dropped.45........" of relation "cell_stats" does not exist
```

Always `JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT
a.attisdropped`. Hit 2026-08-28 in both the sync sequence reset and the
poller's preflight audit -- serving has no dropped columns and passed,
research's `cell_stats` has one and did not. **Eleven unit tests were green
at the time.** The bug was only reachable by executing the SQL against a
real database, which is the general lesson: a query pinned by tests that
never run it is untested.

## Sequences do not advance on an INSERT that supplies an explicit id

So a store that is only ever *copied into* has sequences frozen wherever they
started, and the defect is silent until something *inserts*. Serving held
1,829 `signal_reports` with its sequence at 21. Audit any store you are
about to write to:

```sql
SELECT last_value, (SELECT max(id) FROM signal_reports) FROM signal_reports_id_seq;
```

`cscan poll --serving` now refuses to start when any sequence is behind its
table's max id. See ADR 158's consequence section.

## The fetch cache lies when you change what a key means

`jobs/fetch/yahoo.py` caches every network result to
`data/cache/{source}/{key}.parquet`, keyed on the fetcher's arguments --
`_batch_key` is `tickers_start_end`, `_window_key` is `ticker_start_end`.

That key is a **promise**: for these inputs, this is the answer. It holds
only while the function means the same thing.

**Change what a fetcher returns for unchanged arguments and you must bump
the cache source.** Not the key function -- the `source=` string, which is
the directory name:

```python
@cached(source="yahoo_daily_v2", key_fn=_batch_key)
```

**What this cost, 2026-08-17.** `yf.download`'s `end` is exclusive, so
`cscan nightly` -- scheduled 16:30 local, after the close -- never ingested
the session it had just run after. The fix added a day inside
`_download_daily`. It merged, CI passed, and **the next nightly still
produced stale data**, because every cached entry answered the post-fix
request with the pre-fix result. The fix was correct and simply never ran.

Three properties make this worse than an ordinary stale cache:

- **It survives a merge.** The cache short-circuits the function containing
  the fix.
- **There is no error.** A hit is indistinguishable from a fetch in every
  observable way except duration. `run_market` returned 5 rows and a `runs`
  status of `ok`; the only tell was that it finished in **46 ms**, too fast
  to have touched the network.
- **It fails toward the old behaviour.** A miss would have been loud and
  self-correcting. A hit silently preserves exactly the bug being fixed.

It hid completely because the one path that bypassed the cache was the one
path that worked: a manual recovery script had used `end = today + 1`, a
different key, so daily bars looked correct throughout.

**Rule.** A cache key must capture everything that determines the output,
including the version of the code producing it. These key on arguments
alone, which assumes the semantics are frozen. They are not, so the version
lives in the `source` string and moving it is a manual step in any change
to what a fetcher returns.

**Checking a suspicious result.** Compare the job's duration against a
plausible network cost -- `RATE_LIMIT_PER_SEC = 0.5`, so ~600 tickers cannot
complete in under a minute. A sub-second ingest is a cache read.
`data/cache/yahoo_daily/` and `yahoo_hourly/` hold the pre-`_v2` entries and
are unread; delete them freely.

## `2>&1` on a native exe is terminating under `$ErrorActionPreference = "Stop"`

And it killed the first scheduled nightly. PowerShell 5.1 wraps every stderr
line from a native command in a `NativeCommandError` record. With the
preference at `Stop`, the **first** stderr line becomes a terminating
`RemoteException` -- the exit code is irrelevant, and ordinary progress
output is enough to do it.

On 2026-08-28 at 13:15 `run_nightly.ps1` ran

```powershell
$ErrorActionPreference = "Stop"
& .\.venv\Scripts\cscan.exe nightly 2>&1 | Tee-Object -FilePath $log -Append
```

and died before writing a single line of `cscan` output. The evidence was
misleading in three directions at once: the log held only the wrapper's own
two header lines, so it looked like `cscan` never started; a `bars_daily`
row sat at `status='running'` with no process behind it, so it looked like a
hang; and the row's age (18 minutes) invited the conclusion that the step
was merely slow. It had been dead the whole time.

Scope the preference to the call rather than the file, so genuine cmdlet
errors still stop the script:

```powershell
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& .\.venv\Scripts\cscan.exe nightly 2>&1 | Write-Log
$code = $LASTEXITCODE     # capture immediately; any native call clobbers it
$ErrorActionPreference = $prev
```

**Two related traps in the same file.** `$ErrorActionPreference = "Stop"`
does **not** make a native exe's non-zero exit fail the script, so a wrapper
must `exit $LASTEXITCODE` explicitly or Task Scheduler records success for a
failed job. And `Tee-Object` has no `-Encoding` in 5.1 -- it writes UTF-16LE,
which `tail` and `grep` read as gibberish. Use `Add-Content -Encoding utf8`.

## `npm run build` invalidates a running `next start`

The server holds its chunk hashes in memory; a build rewrites `.next/` with
new ones, so every asset 404s and the page renders as unstyled text. It looks
exactly like broken CSS and is not. **Restart the server after any build**,
and never run `next dev` against the same `.next/` a production server is
serving from. Hit twice, 2026-08-19 and 2026-08-20, both times diagnosed
from the browser console:

```
ChunkLoadError: Loading chunk 974 failed
400 Bad Request  /_next/static/chunks/app/page-<hash>.js
```

Check it in one command -- if the server started before the build, that is
the cause:

```
ls -l --time-style=+%H:%M web/.next/BUILD_ID
Get-CimInstance Win32_Process -Filter "ProcessId=<pid of :3100>" | Select CreationDate
```

## `cscan sync` picks its generation from the research GUC

Not from `core/config.py` and not from `serving_config`. `run_sync` opens
with:

```python
config_hash = conn.execute(
    text("SELECT current_setting('capitalscan.default_config_hash', true)")
).scalar_one()
```

on the **source** connection. So after a deliberate config change the order
that works is:

```
1. edit core/config.py
2. ALTER DATABASE capitalscan SET capitalscan.default_config_hash = '<new>'
3. cscan db sync-config      # writes serving_config
4. cscan sync                # now copies the right generation
```

Skipping step 2 cost 42.5 minutes on 2026-08-29: the sync ran, reported
`synced 7,499,059 rows`, exited 0, and copied **zero rows of the new
generation** -- it faithfully re-copied the old one. Nothing in the output
says which hash it used, and `rows_written` looks identical either way.

**`capscan` IS superuser on research**, unlike on the Pi, so `ALTER DATABASE`
works here without `sudo -u postgres`. That asymmetry is easy to forget after
reading the Pi note below.

**Check all three before trusting a sync:**

```sql
-- research
SELECT current_setting('capitalscan.default_config_hash', true);
-- serving, on the Pi
SELECT config_hash FROM serving_config;
```
```
uv run python -c "from capitalscan.jobs.config import config_hash, resolve_config; print(config_hash(resolve_config()))"
```

## A `psql` session on the Pi reads the wrong config generation

`run_sync` ends by pinning `capitalscan.default_config_hash` on the serving
database and **cannot**: custom `capitalscan.*` parameters need superuser,
and `capscan` is not one. The failure is logged and explicitly called
harmless, because `web/lib/db.ts` sets the hash on every connection from
`serving_config` (ADR 115) -- so the *site* is always right.

What it is not harmless for is **you**, verifying by hand:

    psql on the Pi   current_setting(...)  ->  f66729c7eda212a4   (stale)
    serving_config                         ->  a38d3ca6b58295e8   (live *then*)

**Both hashes above are historical.** They are the 2026-08-26 incident's
values, kept because the *shape* is the lesson. The live generation moved
to `0523841076f47293` on 2026-08-29 (ADR 161). Read this section for the
mechanism, never for a hash to compare against -- get that from
`serving_config`.

Every serving view filters on that GUC, so a manual query silently reads a
generation the site does not serve. Hit 2026-08-26: `SELECT count(*) FROM
v_screen_live WHERE signal_date='2026-08-26'` returned **0** while the page
rendered 138 rows for that date. The instrument was wrong, not the system.

**Fixable, as of 2026-08-27.** `capscan` cannot set a custom parameter,
but `postgres` can, and SSH to the Pi gives you that:

```bash
ssh darischen@192.168.1.30 "sudo -u postgres psql -c \"ALTER DATABASE capitalscan_serving SET capitalscan.default_config_hash = '<hash>'\""
```

**Re-run it after every rebuild**, because `run_sync`'s own pin still fails
-- it connects as `capscan`. Verify the two agree before trusting any
manual query:

```sql
SELECT current_setting('capitalscan.default_config_hash', true),
       (SELECT config_hash FROM serving_config);
```

If they disagree, or you would rather not touch the database default, set
it per session instead:

```sql
SET capitalscan.default_config_hash = '<hash from serving_config>';
```

## Every timestamp in `runs` is UTC

And nothing in the output says so. The research database is
`TimeZone = Etc/UTC`, so `psql` prints `+00` and a row reads `13:00:11`. In
PDT that is **06:00**, seven hours earlier than it looks. Misread once on
2026-08-27: a sync scheduled for 1pm PT was found "already running at 13:00"
and reported as in flight. It had started at 6am and been dead for three
hours.

Convert before quoting a `runs` time, and get the offset from the database
rather than assuming seven -- PST is eight:

```sql
SELECT started_at AT TIME ZONE 'America/Los_Angeles' AS started_pt,
       now() - started_at AS age
FROM runs ORDER BY started_at DESC LIMIT 5;
```

## `status = 'running'` is not evidence a job is running

It means a row was opened and never closed, which is also what a power loss,
a kill and a crash leave behind. A job that dies mid-run cannot write
`finished_at`, so the corpse is indistinguishable from live work by that
column alone. Check for a process and a backend before believing it:

```
SELECT count(*) FROM pg_stat_activity
 WHERE datname='capitalscan' AND pid<>pg_backend_pid() AND state<>'idle';
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*cscan.exe*' }
```

Zero live backends and no `cscan` process means the row is stale. Mark it
`failed` rather than leaving it, because the next reader hits the same trap.

## Editing a module while a job runs gives it a split brain

Found 2026-08-26: `cscan nightly` imported `db_io` at startup, then imported
`sync` **lazily** when the sync step began -- picking up a `sync.py` edited
in between, which called a `db_io.copy_upsert` that the already-cached
`db_io` module did not have. It failed instantly with `module
'capitalscan.jobs.db_io' has no attribute 'copy_upsert'`.

Nothing was corrupted, and the failure was loud. But the rule is wider than
"no concurrent writers": **a long-running job holds whichever modules it has
already imported and picks up the rest from disk as it reaches them.** Edit
freely during a job whose remaining steps you are not touching; do not edit
a module the job has not reached yet.

## `Persistent=true` catches a missed run, not a crashed one

A systemd timer with `Persistent=true` re-fires a run that was **missed**
while the timer was inactive (machine off, laptop asleep). It does **not**
re-fire one that started and then died: the timer writes its elapse stamp
when it fires, so a power loss at 13:40 and a reboot at 13:50 leave the
stamp saying "already fired today" and the next elapse is tomorrow.

This is why `capitalscan-nightly.timer`, `capitalscan-weekly.timer` and
`capitalscan-poller.timer` also carry `OnBootSec=` (ADR 160). On every
boot the service re-fires, and `run_job.sh` calls `cscan resume-check`
(the poller wrapper does its own trading-day + close-time checks) to
decide whether the period's chain still needs to run. Without the boot
trigger a crash between a timer's fire and its work completing costs the
whole period silently.

Verify after a reboot: `systemctl list-timers 'capitalscan-*'` shows the
next elapse, and `journalctl -u capitalscan-nightly --since "$(uptime -s)"`
shows whether the boot fire ran or `skip`ped.

## resume-check compares wall-clock digits, and here is why the tzinfo lies

`scheduled_runs.record` writes `actual_start = datetime.now()` — a naive
Pacific timestamp on a Pacific-clocked machine — into a `timestamptz`
column. It reads back tz-aware, with the Pacific digits intact but a
tzinfo set by the **reading** session: `Etc/UTC` on the old Docker
Postgres, Pacific on wivie's native cluster. So the same row's
`actual_start` prints as `13:15+00` on one machine and `13:15-07` on
another, for a run that happened at 13:15 Pacific both times.

`resume_decision` (ADR 160) strips tzinfo from both `actual_start` and
`now` and compares Pacific wall clocks, which both values are by
construction. Trusting the tzinfo shifted a Sunday-02:00 weekly run to the
previous Saturday evening under a UTC session, reading as "previous
period" and re-running a completed weekly on every boot. If you ever make
`record` write a correct instant, revisit this — the strip becomes wrong
the moment the digits stop being Pacific.

---

## A failed calendar query reads as a market holiday (Pi poller, 2026-09-01)

**Found by a test, not by an outage**, which is the only reason it is
written up in the past tense. `scripts/pi/wait_and_poll.sh` decided whether
to poll with one pipeline:

```bash
if ! sudo -n -u postgres psql -d capitalscan_serving -X -tA \
     -c "SELECT 1 FROM trading_days WHERE d = '$TODAY'" | grep -q 1; then
  say SKIP "$TODAY is not a trading day. Nothing to poll; exiting cleanly."
  exit 0
fi
```

**A `psql` that fails prints nothing to stdout.** `grep -q 1` then finds
nothing, the `!` inverts it, and the script announces *"not a trading
day"* and exits **0**. A database that is down, still starting, or
refusing the role is indistinguishable from a closed market — and the exit
code tells systemd the unit succeeded.

Reproduced in a throwaway container by denying the role login:

```
psql: error: ... FATAL:  role "postgres" is not permitted to log in
[SKIP] 09:42:30 - 2026-09-02 is not a trading day. Nothing to poll; exiting cleanly.
EXIT=0
```

**Why this was going to bite.** `capitalscan-poller.timer` fires at
**00:00**, and Postgres on the Pi is on the same SD card and the same boot
as everything else. A session lost to a slow database start would leave
`journalctl -u capitalscan-poller` reading `[SKIP] ... exiting cleanly` and
`systemctl status` reading success. Nothing anywhere would say the market
had been open and unpolled. ADR 084's `coverage_pct` would not catch it
either, because no `poller_sessions` row is opened at all.

**The Windows script never had this.** `wait_and_poll.ps1` checks
`$LASTEXITCODE` and emits `[ERROR] ... Not polling` with exit 1, separately
from `[SKIP]`. The two wrappers are meant to be interchangeable and this
was a silent divergence between them — worth remembering when reading
"the `.sh` mirrors the `.ps1`" anywhere.

**The fix** splits the two answers. A non-zero `psql` exit is `[ERROR]` and
exit 1; only a successful query returning no row is `[SKIP]` and exit 0.

```bash
CAL=$(sudo -n -u postgres psql ... 2>&1)
if [ $? -ne 0 ]; then say ERROR "cannot reach the serving database ..."; exit 1; fi
if ! printf '%s' "$CAL" | grep -q '^1$'; then say SKIP "..."; exit 0; fi
```

**The general shape, which is the part worth carrying.** *A guard that
cannot distinguish "no" from "I could not tell" is not a guard.* Piping a
query straight into `grep` throws away the exit code, and the failure
direction is whichever answer the empty string happens to mean. The same
pattern is worth looking for anywhere a shell script tests a database for a
condition.

**Deployment is a separate step and was not done here.** The Pi runs its
own clone; the commit landing on `main` changes nothing until
`git pull` runs there. → see the note below.

---

## The Pi is a separate clone, and pushing is not deploying

Recorded 2026-09-01 after fixing the poller bug above and being unable to
deploy it.

`scripts/pi/wait_and_poll.sh` runs from `~/CapitalScan` **on the Pi**. A
commit on `main` reaches it only when someone runs:

```
ssh darischen@192.168.1.30 'cd ~/CapitalScan && git pull --ff-only origin main'
```

**The account names differ per machine, and guessing wastes a deploy.**

    workstation   daris
    wivie         daris        192.168.1.12
    the Pi        darischen    192.168.1.30

`daris@192.168.1.30` returns `Permission denied (publickey,password)`,
which reads exactly like a missing key and is not one -- the key is fine
and the user does not exist. That cost a deploy on 2026-09-01: the fix
above was committed, tested and reported as undeployable, when the only
problem was the username. `~/.ssh/config` has no `Host` entries, so
nothing on the workstation records the difference except this table.

**The gap is real regardless of the cause.** On 2026-08-31 a `git pull` on
the Pi brought **51 commits**, and until it ran the Pi was resolving a
stale `config_hash`. On 2026-09-01 it was **21 commits** behind by evening
-- every fix of that day, including the calendar guard its 00:00 timer
depends on.

**So a poller fix is not finished at the push.** Check what the Pi actually
has:

```
ssh darischen@192.168.1.30 'cd ~/CapitalScan && git log --oneline -1'
```

The same applies to `wivie` and to any `scripts/pi/` or `scripts/systemd/`
change. Three machines, three clones, and only the workstation's is the one
you are editing.
