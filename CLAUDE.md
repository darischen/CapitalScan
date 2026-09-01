# CapitalScan

Bollinger Band + Stochastic Oscillator event-study engine for US large-cap equities and ETFs.

**Advisory only. No execution path exists or may be added.**

**The universe is *seeded* from S&P 500 membership, not restricted to it.**
`run_tickers_refresh` scrapes the Wikipedia constituent table and ADR 035
keeps the historical union for survivorship reasons, so that is where the
712 rows come from. Nothing downstream requires index membership: the four
criteria in `UniverseParams.required_criteria` read price, SMA200, slope and
relative return, and market cap resolves from SEC XBRL by CIK **with a Yahoo
fallback for names that have none** (68 tickers today, `shares_outstanding.source`).

QQQ is the proof and it was added by hand: no CIK, no sector, 5,280 daily
bars, 66 universe evaluations, `in_trade` true at $289B, and 29,343 events.
A ticker outside the S&P 500 participates fully once its rows exist. Planned
expansion to other markets and more ETFs is in `BACKLOG.md`.

---

## Before writing any code

Read `docs/DECISIONS.md`. It holds 164 ADRs. They are decisions, not suggestions.

If a task appears to require contradicting one, **stop and ask.** Do not work around it.

| Question | Document |
|---|---|
| Why is it this way? | `docs/DECISIONS.md` |
| What is known and not done? | `docs/BACKLOG.md` |
| What is it? | `docs/DESIGN.md` |
| What do I build next? | `docs/BUILD.md` |
| How do I know it works? | `docs/TESTS.md` |
| What happened when we ran it? | `docs/RESULTS.md` |
| What broke before, and the fix? | `docs/OPERATIONS.md` |
| How long does a job take? | `docs/TIMINGS.md` |

`docs/OPERATIONS.md` and `docs/TIMINGS.md` hold the full incident writeups and
measured durations. This file keeps the one-line rule for each and points there.

---

## The three machines

**Read this before any rule below that names a path, a service or a
scheduler.** Much of this file was written when there was one machine, and
a rule that was true everywhere in August is now true on one box.

| | workstation (`DESKTOP-3MBOCAU`) | laptop (`wivie`) | the Pi |
|---|---|---|---|
| address | 192.168.1.14 | 192.168.1.12 | 192.168.1.30 |
| OS | Windows 11 | Debian 13 | Raspberry Pi OS |
| role | heavy research | scheduled research | serving |
| Postgres | **16.14 in Docker**, `capitalscan-postgres` | **17.11 native**, systemd | 17.11 native, systemd |
| scheduler | Task Scheduler | systemd timers | systemd timers |
| runs | backtests, sweeps, rebuilds, `nightly` today | `nightly`/`weekly`/`monthly` after cutover | poller, serving DB, web app |

**The research database is 16.14 and both Debian boxes are 17.11**, so the
cutover crosses a major version. `pg_restore` in that direction is fine;
the reverse is not, so a dump taken on `wivie` cannot be loaded back onto
the container without a downgrade path. Verified 2026-09-01.

All three addresses are DHCP-reserved (2026-09-01), so an address in a
config file stays valid.

**The desktop is not being retired.** It leaves the *scheduled* role at the
cutover and stays the heavy-research box — it is 1.58x faster than the
laptop on the real hot path (`scripts/cpu_bench.py`, 2026-08-28) and that
gap is a power budget, not tuning. So this file is a **two-machine
document, permanently**, not a handoff. Expect both to be live.

**The migration is one change, by design.** The only edit required to move
the scheduled role is **what the Pi's `.env.local` points at**, plus the
one-time `pg_dump`/`pg_restore` of research. See `docs/SETUP.md` Part C1;
everything else is already staged on `wivie`.

**The bar is functionally identical, not literally identical.** Measured
2026-09-01: Postgres 16.14 against 17.11, Python 3.14.3 against 3.13.5.
Both accepted — `pyproject.toml` supports the whole range and nothing in
the cutover copies an interpreter between machines. Do not "fix" either
one. Do not assume a version matches because the setup was scripted;
check when a version could plausibly matter.

**The two places they genuinely cannot be identical**, and therefore the
only labels that carry weight below:

1. **Docker Postgres against native Postgres.** The container does not
   restart after a reboot and has failed a nightly for exactly that reason
   (2026-08-30). Native systemd Postgres removes the whole failure class.
2. **Task Scheduler against systemd.** Task Scheduler needs an interactive
   logon and records success for a failed job unless the wrapper propagates
   `$LASTEXITCODE`. systemd does neither.

Where a rule applies to one machine it now says so. Where it says nothing,
it applies everywhere.

---

## Before running anything

**Never run bare `pytest` or `uv run pytest`.** `pyproject.toml` sets `testpaths = ["capitalscan/tests"]`, so a bare invocation collects `capitalscan/tests/integration/`, which runs `TRUNCATE TABLE ... CASCADE` against live production data (4.5M+ rows in `bars`). `test_ingest.py` and `test_compute.py` truncate `bars` directly; `test_poll.py` truncates `tickers`, which CASCADEs to `bars`.

The only safe invocation: `uv run pytest capitalscan/tests/unit capitalscan/tests/property`. Never invoke anything under `capitalscan/tests/integration/` against the real database.

**Run the fast tier exactly as CI runs it, or you will not reproduce it.** All four steps, whole-repo scope, no shortcuts:

```
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest capitalscan/tests/unit capitalscan/tests/property -p no:randomly \
  --hypothesis-profile=ci_fast --cov=capitalscan/core --cov-report=term-missing --cov-fail-under=90
```

Three traps, all hit for real on 2026-08-09:

- **`ruff check capitalscan/` is not `ruff check .`.** It skips `db/migrations/`, where a long `op.execute(...)` line failed E501 on a branch for three days and reached `main` unnoticed.
- **`ruff format --check` is a separate gate from `ruff check`.** A file can pass lint and still fail formatting, and nothing warns you.
- **`uv run mypy` bare is wider than any explicit path list.** It reads `pyproject.toml` and covers 137 files including tests; `mypy capitalscan/core capitalscan/jobs` covers 43 and proves much less.

**No `cscan db migrate` or `uv sync`/`uv add` while a job is running.** Migrate takes an ACCESS EXCLUSIVE lock against a live writer — true everywhere. The `uv` half is Windows-specific: `uv sync`/`uv add` locks `.venv` files a running process holds open. On Linux the same command silently swaps a file the running job still has mapped, which fails later and further away, so the rule stands on both for different reasons.

**Editing a module mid-job gives it a split brain.** A long-running job holds whichever modules it has already imported and picks up the rest from disk as it reaches them. Edit freely during a job whose remaining steps you are not touching; do not edit a module the job has not reached yet. → `OPERATIONS.md`

---

## Reaching the database

**Everything in this section is about the workstation.** On `wivie` and the
Pi, `psql` is on PATH, there is no container, and `sudo systemctl status
postgresql` answers the "is it up" question. The commands below are written
out in full because of a Windows-specific shell limitation, not because
long paths are the house style.

**Bash on the workstation does not see user or system environment
variables.** Anything not on the default PATH needs its full path --
`psql`, `docker.exe`, `node`.

**Postgres runs in Docker on the workstation** as container
`capitalscan-postgres`, mapped to 5432. A *separate* native PostgreSQL 18
service listens on **15432** and rejects the `capscan` password. So
`connection refused` on 5432 means the container is down, not that the
server moved. `docker` is not on PATH in agent shells:

```
"C:\Program Files\Docker\Docker\resources\bin\docker.exe" ps -a
"C:\Program Files\Docker\Docker\resources\bin\docker.exe" start capitalscan-postgres
```

Reach Postgres directly:

```
PGPASSWORD=capscan "/c/Program Files/PostgreSQL/18/bin/psql" -h localhost -U capscan -d capitalscan
```

Prefix `SET max_parallel_workers_per_gather=0;` if a **query** hits
`could not resize shared memory segment ... No space left on device`. That
setting does **not** cover `VACUUM`/`REINDEX`/`CREATE INDEX`; use the
command's own option, `VACUUM (PARALLEL 0, ANALYZE) tablename`.

Rules from past incidents (full writeups in `OPERATIONS.md`):

- **Any change to how the database is reached needs a connect-latency check.** *(Workstation; the mechanism is general, the fix is container-specific.)* Binding the container IPv4-only (`0.0.0.0:5432`) made `localhost` resolve to `::1` first and cost 2 s per connect, a 4-5x backtest regression with no error in any log. Bind both families: `-p 0.0.0.0:5432:5432 -p "[::]:5432:5432"`. Every timing measured 2026-08-24 to 2026-08-26 is inflated by this.
- **A firewall rule scoped `-Profile Private,Domain` stops applying when the network profile flips to Public**, and only the remote writer fails. *(Workstation only — Windows Firewall has no equivalent on the Debian boxes.)* Check `Get-NetConnectionProfile` first; fix with `Set-NetConnectionProfile -NetworkCategory Private`, not `-Profile Any`.
- **A client-side network symptom is not evidence of a client-side cause.** `server closed the connection` from a remote writer was a stalled checkpoint (research ran the 1 GB default `max_wal_size` until 2026-08-29, now 4 GB, `synchronous_commit=on`). Read `docker logs capitalscan-postgres` before blaming the link (`journalctl -u postgresql` on the Debian boxes). WAL and autovacuum tuning live on the server, not in a migration — which is why they must be re-applied by hand on `wivie` rather than arriving with a `pg_restore`.
- **Run `VACUUM (PARALLEL 0, ANALYZE)` after any bulk write**, and check `n_live_tup` against a real `count(*)` when a job is inexplicably slow. `indicators` once held 4M rows while the planner believed 80k (never autoanalyzed after the 2026-08-21 crash); `indicators` and `events` now carry absolute autovacuum thresholds.
- **`psql` exits 0 on the shared-memory error** (it goes to stderr). Verify maintenance via `pg_stat_user_tables.last_vacuum` / `last_analyze`, never the exit code.
- **A `pg_attribute` sweep must exclude dropped columns** (`AND a.attnum > 0 AND NOT a.attisdropped`); `pg_get_serial_sequence` raises on a `........pg.dropped.N........` placeholder and aborts the statement.
- **Sequences do not advance on an explicit-id INSERT**, so a copy-only store has frozen sequences. `cscan poll --serving` refuses to start when any sequence is behind its table's max id (ADR 158).

---

## Caches and stale reads

**The fetch cache is keyed on arguments, which assumes the function's meaning is frozen.** `jobs/fetch/yahoo.py` caches to `data/cache/{source}/{key}.parquet`. **Change what a fetcher returns for unchanged arguments and you must bump the `source=` string** in `@cached(source="yahoo_daily_v2", key_fn=...)` -- not the key function. A cache hit is indistinguishable from a fetch except by duration, and it fails toward the old behaviour, so a correct fix can merge, pass CI, and never run. → `OPERATIONS.md`

**Spotting a cache hit:** `RATE_LIMIT_PER_SEC = 0.5`, so ~600 tickers cannot ingest in under a minute. A sub-second ingest is a cache read. `data/cache/yahoo_daily/` and `yahoo_hourly/` hold pre-`_v2` entries, unread; delete freely.

---

## Windows and PowerShell — *workstation only*

**`2>&1` on a native exe is terminating under `$ErrorActionPreference = "Stop"`.** PowerShell 5.1 wraps the first stderr line in a `RemoteException`; ordinary progress output is enough, and the exit code is irrelevant. It killed the first scheduled nightly before a line of output. Scope the preference to the call:

```powershell
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& .\.venv\Scripts\cscan.exe nightly 2>&1 | Write-Log
$code = $LASTEXITCODE     # capture immediately; any native call clobbers it
$ErrorActionPreference = $prev
```

`$ErrorActionPreference = "Stop"` does **not** make a native exe's non-zero exit fail the script -- a wrapper must `exit $LASTEXITCODE` or Task Scheduler records success for a failed job. `Tee-Object` has no `-Encoding` in 5.1 and writes UTF-16LE; use `Add-Content -Encoding utf8`. → `OPERATIONS.md`

**`npm run build` invalidates a running `next start`.** The server holds its chunk hashes in memory; a build rewrites `.next/` and every asset 404s, rendering as unstyled text that looks like broken CSS. Restart the server after any build; never point `next dev` at a `.next/` a production server is serving. → `OPERATIONS.md`

---

## Long jobs

Budgets, so nobody starts one blind. Per-step tables, regimes, and the history of every figure that was wrong are in `docs/TIMINGS.md` -- **a step has regimes, and one measurement is one regime; check `runs` for the distribution.**

**Every figure below was measured on the workstation.** `wivie` is slower on
CPU-bound phases and the multiplier is **unmeasured** — the 1.58x in
BACKLOG.md is for a different laptop (the Flow X13), not this one. Run
`scripts/cpu_bench.py` on `wivie` before quoting a budget there. Two of this
project's hardware estimates were wrong until measured, so predict nothing.
Network-bound steps (the fetchers, most of `nightly`) should be close to
parity.

| job | budget |
|---|---|
| `cscan backtest --workers 8`, full universe (~1,470 tickers) | **~2 h** (compute 82 min, finalize 4 min, harness 36 min) |
| `cscan nightly`, cold | **35-40 min** (not 21; `shares` alone is ~10 min at this universe size) |
| `cscan weekly` | ~36 min (runs the backtest, skips the harness) |
| `cscan bars --daily --lookback 8000` | ~11 min / 521 tickers |
| `cscan bars --hourly --backfill`, all tickers | ~4.5-5.5 h, no incremental path |
| `cscan universe --quarter` x 66 | ~20 min |

- **Use `--phase` for anything long.** `compute` is resumable, checkpointed per `--chunk-size` (default 25); `_chunk_already_done` keys on `(config_hash, chunk, of)`, so **keep `--chunk-size` identical across restarts** or every chunk re-runs. Each phase writes its own `runs` row (`backtest_compute` per chunk, `backtest_finalize`, `backtest_harness`); `notes` carries `harness passed` or the failing check.
- **`compute`'s `cofire_count` is only correct within a chunk** and is excluded from that write. `finalize` is the whole-universe pass that corrects it, and only if `compute` finished for the config.
- **`cscan indicators` writes nothing until it finishes** -- it collects across all tickers then upserts once. Querying mid-run returns the pre-run count and looks exactly like a hang. Pass `--workers 8`; it defaults to 1.
- **Never run `cscan universe --quarter` while a backtest runs.** Not locking -- determinism: workers resolving eligibility against a `universe` that changes mid-run violate ADR 060.
- **`runs` timed the write phase only before 2026-08-18.** Check `started_at` before quoting an old duration.

---

## Config generation and sync

**`cscan sync` picks its generation from the RESEARCH database's GUC**, not from `core/config.py` and not from `serving_config`. After a deliberate config change:

```
1. edit core/config.py
2. ALTER DATABASE capitalscan SET capitalscan.default_config_hash = '<new>'
3. cscan db sync-config      # writes serving_config
4. cscan sync                # now copies the right generation
```

Skipping step 2 re-copies the old generation, reports `synced N rows`, and exits 0 -- nothing in the output names the hash. `capscan` IS superuser on research, so `ALTER DATABASE` needs no `sudo -u postgres` here (it does on the Pi).

**Check all three agree before trusting a sync or a manual query:**

```sql
SELECT current_setting('capitalscan.default_config_hash', true);   -- research
SELECT config_hash FROM serving_config;                            -- serving, on the Pi
```
```
uv run python -c "from capitalscan.jobs.config import config_hash, resolve_config; print(config_hash(resolve_config()))"
```

**A `psql` session on the Pi reads the wrong generation.** `run_sync`'s pin on the serving DB fails silently (needs superuser); the site is fine because `web/lib/db.ts` sets the hash per connection from `serving_config` (ADR 115), but a hand query reads a generation the site does not serve. Re-pin after every rebuild via SSH as `postgres`, or `SET capitalscan.default_config_hash = '<hash>'` per session. → `OPERATIONS.md`

---

## Reading `runs`

**Every timestamp is UTC and nothing says so.** The research DB is `TimeZone = Etc/UTC`; a row reading `13:00` is 06:00 PDT. Convert, and get the offset from the DB (PST is eight, not seven):

```sql
SELECT started_at AT TIME ZONE 'America/Los_Angeles' AS started_pt,
       now() - started_at AS age
FROM runs ORDER BY started_at DESC LIMIT 5;
```

**`status = 'running'` is not evidence a job is running.** A crash, kill, or power loss leaves the same row -- `finished_at` never gets written. Check for a live backend and a process:

```
SELECT count(*) FROM pg_stat_activity
 WHERE datname='capitalscan' AND pid<>pg_backend_pid() AND state<>'idle';
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*cscan.exe*' }
```

Zero backends and no `cscan` process means the row is stale. Mark it `failed`, because the next reader hits the same trap.

---

**Verify before you assert.** Query the database rather than trusting a prior report, including this one — several confident claims in earlier session reports did not hold up under direct measurement.

---

## Non-negotiable invariants

1. **`core/` performs no IO.** No database, no HTTP, no file reads, no clock access. `jobs/` and `research/` own all IO.
2. **One signal implementation.** `jobs/` and `research/` both import `core/signals.py`. Never write a second band comparison anywhere.
3. **Indicators are read at t−1, never t.** Enforced in `core/signals.py` and again in the `events` job. This is the highest-risk silent failure in the system.
4. **Never fill, forward-fill, or interpolate a null.** Drop the row and log it to `bar_rejects` with a reason.
5. **`split_key` is assigned at event creation, never at query time.**
5b. **No view or query may join statistics on an event's own `split_key`.** Live events carry `split_key = 'holdout'`; inheriting it would surface holdout numbers continuously. Serving views hardcode `split_key = 'validate'`. `cell_id` is derived from component columns, never stored on `events`.
6. **Every generated row carries `run_id` and `git_sha`.**
7. **No broker client, no order placement, no brokerage credentials.** The absence is the safety property, not a disabled flag.
8. **Every response carrying a probability carries `n_eff` and a confidence interval.**
9. **No magic numbers outside `core/config.py`.** This includes thresholds that happen to match a default elsewhere. A literal `80.0` in the exit path while `stoch_overbought` is sweepable lets entry and exit disagree inside one backtest, and the output looks fine.
10. **`core/config.py` holds dataclasses only.** Sole import is `dataclasses`. Resolution lives in `jobs/config.py`. Invariant 1 applies to the config module too.

---

## Platform

**Two platforms, not one.** The workstation is native Windows; `wivie` and
the Pi are Debian. Code must run on both — that is what `scripts/run_job.ps1`
and `scripts/run_job.sh` exist for, and why no script under `scripts/` may
contain an absolute path to the repo, the venv or `psql`.

**The two boxes are not on the same Python, and that is accepted**,
measured 2026-09-01:

| | workstation | `wivie` |
|---|---|---|
| Python | **3.14.3** | **3.13.5** |
| `mp.get_start_method()` | `spawn` | `fork` |

`pyproject.toml` pins only `requires-python = ">=3.11"`, so `uv` resolved
whatever each machine had. Deliberately left alone: the code supports the
range and the rule below already forbids depending on the start method.
Worth knowing, not worth aligning.

`ProcessPoolExecutor` therefore uses **spawn on the workstation and fork on
`wivie` today.** Write for spawn: it is the stricter of the two, and code
that quietly depends on fork breaks only on Windows. Every job module must
be importable with no side effects, every entry point needs
`if __name__ == "__main__":`, and workers open their own database
connections because connections are not picklable. Getting this wrong causes
recursive process creation, which looks like a hang.

### Scheduling

| machine | mechanism | state |
|---|---|---|
| workstation | Task Scheduler, catch-up enabled | `CapitalScan nightly` registered and `Ready`. `weekly`/`monthly` **not registered** — `install_schedule.ps1` would do it and has never been run here. |
| `wivie` | systemd timers, `Persistent=true` | All three rendered into `/etc/systemd/system` and `daemon-reload`ed, **timers `disabled`.** `systemctl enable --now` is the cutover step. |
| Pi | systemd timer | `capitalscan-poller.timer`, live, fires 00:00 PT. |

The workstation task runs `scripts/run_nightly.ps1`, now a one-line shim to
`run_job.ps1`. **First real Task Scheduler firing of the new wrapper was
2026-09-01 13:15**, and `resume-check` correctly decided to run.

**Two Windows-only scheduler traps**, neither of which exists under systemd:

- Task Scheduler **records success for a failed job** unless the wrapper
  ends with `exit $LASTEXITCODE`. `$ErrorActionPreference = "Stop"` does not
  make a native exe's non-zero exit fail the script.
- It needs an **interactive logon** to run as the user, which is why a
  headless box wants systemd rather than a ported XML.

On 2026-08-21 nothing was registered and this section still claimed a live
schedule; the false claim was quoted back as fact in a session before anyone
ran `Get-ScheduledTask`. Run it, or `systemctl list-timers`, rather than
trusting this table.

**The poller moved to the Pi on 2026-08-28 (ADR 158), and this changes what
you may run during market hours.** It is the one genuinely scheduled thing
in the system now: `capitalscan-poller.timer` fires at 00:00 PT and
`scripts/pi/wait_and_poll.sh` waits until 06:45 before polling to the
13:00 close. `journalctl -fu capitalscan-poller` on the Pi is the live
view; `--since today` reads a finished session.

`cscan poll --serving` writes the **serving** store, not research. That is
the whole point: **research has no live writer during market hours**, so
the research machine is free to rebuild, run nightly, be updated, or be
shut down between 06:30 and 13:00. The old rule -- no second writer on
`events` while the poller runs -- no longer binds, because the poller is
not writing that database.

**"The research machine" means whichever box holds the research database**,
the workstation today and `wivie` after the cutover. Nothing in this
section depends on which one it is.

Three consequences worth knowing before relying on it:

- **Never run `scripts/wait_and_poll.ps1` and the Pi timer at the same
  time.** As of 2026-08-31 the `.ps1` also runs `cscan poll --serving` and
  writes serving directly -- a true drop-in for the Pi when it skips a day,
  same guards, same target, same one-run-id-per-day. That is exactly why
  the two must not overlap: both write serving and would double-write. The
  Pi's unit is a `oneshot` done by ~13:00, so a later manual `.ps1` run is
  safe; a concurrent one is not. Before 2026-08-31 the `.ps1` wrote
  research and pushed per tick, a different path with no staleness or
  sequence guard -- that is the version that broke on 2026-08-31.
- **The Pi's `core/config.py` must stay at baseline.** `cscan poll`
  resolves config, so an ablation-arm config left on the Pi would have the
  poller writing events under that arm's hash and the site would show
  nothing. Verified before enabling: the Pi resolves the live hash, which
  is what `serving_config` pins. **Run arms on the workstation** — that is
  the one job that stays there permanently, because it is the fastest box
  and arms are the longest thing the project runs.

  **The live hash is `0523841076f47293` as of 2026-08-29** (arm `t5_atr20`:
  `target_pct` 0.05, `stop_atr_k` 2.0; commit `42ae20a`, RESULTS
  2026-08-29). It moved from `a38d3ca6b58295e8`, which the `OPERATIONS.md`
  and `TIMINGS.md` anecdotes still name and which stays resident as the
  prior serving generation. Confirm against all three before trusting a
  manual query: `serving_config`, the research GUC, and
  `config_hash(resolve_config())`.
- **Nightly must still run on the research machine**, because
  `sync.pull_live_records` brings the poller's durable rows back --
  `runs` (scoped `job='poll'`), `signal_reports` and `poller_sessions`.
  Those three are permanent and research is where analysis reads them
  (ADR 084's `coverage_pct`). Skip nightly and research silently stops
  accumulating them.

**No SSH tunnel is involved.** `--serving` resolves `DATABASE_URL_SERVING`,
which is localhost on the Pi, and `pull_live_records` runs inside nightly on
the workstation. The reverse tunnel used for the arm-3 experiments is not
part of this path.

## Conventions

- pandas, `float64` in compute, `numeric(12,4)` / `numeric(12,6)` in Postgres
- One ticker per `core/` function call
- DataFrame column names == SQL column names, no translation layer
- Never mutate in place, always return a new object
- Round prices to 4 decimals before any comparison
- `rich.progress` for anything over 30 seconds; checkpoint anything over 10 minutes

---

## Price series

Two series, different purposes. Getting this wrong corrupts every signal.

| Purpose | Series |
|---|---|
| Indicator computation (bands, stochastic, ATR, SMA, drawdown) | Split-adjusted `close` |
| Return measurement | Total-return `adj_close` |
| Live band comparison | Split-adjusted |

**Exception:** `realized_vol` takes total-return adjusted close, because it measures return dispersion rather than price level. This is the only place the two mix inside one module. It requires a comment in the code.

---

## Testing

Write the test before the implementation for anything in `core/`.

Coverage gate: **90% on `core/` only.** No repo-wide target.

Five tests carry the correctness load. Do not weaken any of them:

1. Look-ahead: the shift ladder plus the **signature probe**. The probe is the real guarantee — `detect` may read only `low`, `high`, `ts`, `ticker` from the bar, and receives one indicator row, never a frame. Never widen that signature.
2. Signal path parity (`detect` vs `breach_live` on a simulated intraday path)
3. Determinism (identical config → identical output)
4. Exit invariants (property-based). `mfe >= realized_return` is the sharp one. MFE is **not** clamped at zero — negative MFE is real and DESIGN §5.6 depends on it.
5. Split leakage (structural date bounds + purged fold check)

See `docs/TESTS.md` §3.

---

## Alembic

**The user has not used Alembic before. Treat every migration task as a teaching moment.**

For every migration:

- Explain what the command does *before* running it
- Show the generated file and walk through each line
- Explain what `upgrade()` and `downgrade()` do in that specific case
- Show how to verify (`cscan db status`, `\d tablename`)
- **Never** run `--autogenerate` without reading the output aloud first — it misses index and constraint changes and sometimes produces destructive operations

**Never invoke bare `alembic`.** `alembic.ini:89` still holds the template placeholder `sqlalchemy.url = driver://user:pass@localhost/dbname`, and `db/migrations/env.py` only overrides it from `CAPSCAN_ALEMBIC_URL` / `DATABASE_URL_RESEARCH` **in the environment**. `cscan db *` loads `.env.local` first (`jobs/db.py::_load_env`) and sets that variable; a bare shell has not, so `alembic current` dies with `NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:driver`. This line previously said "verify with `alembic current`" and cost a real session that exact traceback.

| Want | Run |
|---|---|
| Current revision | `cscan db status` |
| Apply | `cscan db migrate` |
| Undo one | `cscan db rollback --yes` |
| Push `ExitParams` to the serving views | `cscan db sync-config` |
| Provision the MCP read-only role | `cscan db grant-readonly --password <pw>` |

**`cscan db sync-config` is not optional after a threshold change.**
`v_positions` reads its exit policy from the one-row `serving_config` table
rather than from SQL literals (ADR 115), so sweeping `exit_stoch_threshold`
moves the backtest and leaves the position page reporting the old number
until this runs. `test_v_positions_config.py::test_the_stored_row_matches_
the_live_config` fails when the two disagree and names this command.

`cscan db migrate` applies to **both** databases by default. Single-target requires an explicit flag. Forgetting the second database is the main way this goes wrong. A target whose env var is unset is skipped with a visible `skip <target>: <VAR> not set`, so read the output rather than assuming both ran.

---

## Frontend

**Read the frontend-design skill before writing any component.** It is Anthropic's general design-guidance skill: how to avoid templated-looking output, pair a display and body face deliberately, pick one signature element, spend boldness in one place.

**It carries no CapitalScan tokens.** This line and `DESIGN.md` §11.7 both claimed it held "this environment's design tokens" until 2026-08-18, when someone read it. There is no palette, no type scale, and no spacing system in it. The design constraints are already here and in `DESIGN.md` §11.6-11.9: dense instrument panel, monospace numerals, dark by default, colour as meaning, five states per data component.

The skill's own rule settles how the two combine: *"where the brief pins down a visual direction, follow it exactly."* The brief is pinned. The skill governs process, not palette.

It is a plugin skill, so it lives under `~/.claude/plugins/`, not `~/.claude/skills/`. Invoke it as `frontend-design:frontend-design`.

Design direction: dense instrument panel, not a marketing page. Monospace for all numbers. Dark by default. Color carries meaning only. Every data component handles five states: loading, empty, suppressed, stale, error.

`lightweight-charts` for price and stochastic panels. `recharts` for statistical charts.

---

## MCP server

`cscan mcp serve` (127.0.0.1:8787), `cscan mcp tools` to print the generated
schemas. Full setup, token rotation, and client configuration in
`docs/MCP_SETUP.md`.

Three things that bite:

- **It refuses to start without `MCP_BEARER_TOKEN`.** By design (ADR 027).
- **`DATABASE_URL_MCP` unset is a development-only state.** The server falls
  back to the read-write research role and says so. `cscan db grant-readonly`
  provisions `capscan_ro`.
- **Behind a domain, pass `allowed_hosts`.** The SDK's DNS-rebinding guard
  answers `421 Misdirected Request` on a Host mismatch, which reads like a
  routing fault rather than a policy one.

`mcp/` may not import `sqlalchemy` or `db_io`, and no tool may make two
handler calls or branch after one. Both are tests, not conventions — ADR 027
requires the server to add no query logic.

## Chat and tools

The response validator requires **sourcing**, not advice avoidance. This is an advisory system; notifications and reports both state what fired and what historically followed.

Passes:
> TSM fired confluence-low today in the 10-20% drawdown bucket. That cell resolved up 3% within 5 sessions in 51% of 340 effective cases against a 39% baseline, CI 46-56.

Fails:
> TSM looks like a good buy here.

Absolute carve-out, no exceptions: **no claims about the user's financial situation, tax position, or suitability.** Nothing in the database sources those.

---

## What "done" means

A task is done when its acceptance criterion in `docs/BUILD.md` passes, not when the code looks finished.

Phase gates are in `docs/TESTS.md` §10. Do not advance past a gate that has not passed.

**Holdout data is evaluated exactly once, at the end, and published whatever it says.**
