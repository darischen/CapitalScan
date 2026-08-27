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

Read `docs/DECISIONS.md`. It holds 150 ADRs. They are decisions, not suggestions.

If a task appears to require contradicting one, **stop and ask.** Do not work around it.

| Question | Document |
|---|---|
| Why is it this way? | `docs/DECISIONS.md` |
| What is known and not done? | `docs/BACKLOG.md` |
| What is it? | `docs/DESIGN.md` |
| What do I build next? | `docs/BUILD.md` |
| How do I know it works? | `docs/TESTS.md` |
| What happened when we ran it? | `docs/RESULTS.md` |

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

- **`ruff check capitalscan/` is not `ruff check .`.** It skips `db/migrations/`, where a long `op.execute(...)` line had been failing E501 on a branch for three days. It reached `main` unnoticed because every local check was scoped to `capitalscan/`.
- **`ruff format --check` is a separate gate from `ruff check`.** A file can pass lint and still fail formatting, and nothing warns you.
- **`uv run mypy` bare is wider than any explicit path list.** It reads `pyproject.toml` and covers 137 files including tests; `mypy capitalscan/core capitalscan/jobs` covers 43 and proves much less.

**Bash on this machine does not see user or system environment variables.**
Anything not on the default PATH needs its full path -- `psql`, `docker.exe`,
`node`. This is why every command below is written out in full.

**Postgres runs in Docker** as container `capitalscan-postgres`, mapped to
5432. A *separate* native PostgreSQL 18 service listens on **15432** and
rejects the `capscan` password. So `connection refused` on 5432 means the
container is down, not that the server moved -- check it before diagnosing
anything else:

```
"C:\Program Files\Docker\Docker
esourcesin\docker.exe" ps -a
"C:\Program Files\Docker\Docker
esourcesin\docker.exe" start capitalscan-postgres
```

It exited 255 unprompted on 2026-08-21 at ~01:00 PT, killing a 1h55m
backtest. No OOM, no disk error in its log, nobody touched it. Crash
recovery lost nothing. Long jobs should be assumed interruptible.

`docker` is not on PATH in agent shells. Reach Postgres directly:

```
PGPASSWORD=capscan "/c/Program Files/PostgreSQL/18/bin/psql" -h localhost -U capscan -d capitalscan
```

Prefix `SET max_parallel_workers_per_gather=0;` if a **query** hits a shared-memory error:

```
could not resize shared memory segment ... No space left on device
```

**A table can go stale forever without anything reporting it.** Found
2026-08-25: `indicators` held **4,011,351 rows while the planner believed
80,043** — a 50x underestimate on every plan touching it. The symptom was a
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
255 unprompted on 2026-08-21 (recorded above), and autoanalyze fires at
`50 + 0.1 * n_live_tup` — about 8,054 at the stale estimate. The only
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
`ProcessPoolExecutor` bug — none of which it was.

**That setting does not cover maintenance commands.** It governs query-time gather nodes only. `VACUUM` parallelises index cleanup under `max_parallel_maintenance_workers` and ignores it entirely, so a large vacuum fails with the same message and the same fix does nothing. Use the command's own option:

```
VACUUM (PARALLEL 0, ANALYZE) tablename;
```

`REINDEX` and `CREATE INDEX` take `max_parallel_maintenance_workers` too.

**The failure is silent.** `psql` exits **0** on this error — it goes to stderr, so a piped or filtered invocation swallows it and the command looks like it worked. On 2026-08-17 two `VACUUM` runs on `path` failed this way and were only caught because `pg_stat_user_tables.last_vacuum` was still NULL with 57.3M dead tuples. Verify maintenance with the catalog, never with the exit code:

```sql
SELECT relname, n_dead_tup, last_vacuum, last_analyze
FROM pg_stat_user_tables WHERE relname = 'path';
```

## The fetch cache lies when you change what a key means

`jobs/fetch/yahoo.py` caches every network result to
`data/cache/{source}/{key}.parquet`, keyed on the fetcher's arguments —
`_batch_key` is `tickers_start_end`, `_window_key` is `ticker_start_end`.

That key is a **promise**: for these inputs, this is the answer. It holds
only while the function means the same thing.

**Change what a fetcher returns for unchanged arguments and you must bump
the cache source.** Not the key function — the `source=` string, which is
the directory name:

```python
@cached(source="yahoo_daily_v2", key_fn=_batch_key)
```

**What this cost, 2026-08-17.** `yf.download`'s `end` is exclusive, so
`cscan nightly` — scheduled 16:30 local, after the close — never ingested
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
plausible network cost — `RATE_LIMIT_PER_SEC = 0.5`, so ~600 tickers cannot
complete in under a minute. A sub-second ingest is a cache read.
`data/cache/yahoo_daily/` and `yahoo_hourly/` hold the pre-`_v2` entries and
are unread; delete them freely.

**`npm run build` invalidates a running `next start`.** The server holds
its chunk hashes in memory; a build rewrites `.next/` with new ones, so
every asset 404s and the page renders as unstyled text. It looks exactly
like broken CSS and is not. **Restart the server after any build**, and
never run `next dev` against the same `.next/` a production server is
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

No `cscan db migrate` or `uv sync`/`uv add` while a job is running. Migrate takes an ACCESS EXCLUSIVE lock against a live writer; `uv sync`/`uv add` on Windows locks `.venv` files a running process holds open.

Long jobs, measured, so nobody starts one blind:

- `cscan backtest --workers 8`, full universe: **~2 hours**, measured end to
  end on 2026-08-26 against **1,470 tickers / 1,365,000 events** under
  `a38d3ca6b58295e8`, on a machine where `localhost` connects in 40ms (see
  the IPv6 note above -- every timing taken between 2026-08-24 and
  2026-08-26 is inflated and should be distrusted).

  | phase | wall clock | output |
  |---|---|---|
  | `compute` (59 chunks x 25 tickers, 8 workers) | **81.9 min** | 1,365,000 rows |
  | `finalize` (cross-ticker cofire) | **3.6 min** | 1,365,000 rows |
  | `harness` (8 workers, parallel) | **35.7 min** | `harness passed` |

  Compute averaged 83s per chunk. **The harness is the number that was most
  wrong**: this file said 4h19m, which was single-threaded on 590 tickers.
  It is 35.7 min against 2.5x the tickers -- roughly the worker count,
  which is what parallelising it was supposed to buy.
  - **The harness is parallel now, and this line said otherwise until 2026-08-25.** It read "the validation harness is single-threaded and takes the rest regardless of worker count". That was true between `78d1e38` (which reverted a parallel harness that deadlocked passing frames to workers) and `0b2cc00`, which re-parallelised it by spooling ticker slices to **parquet** instead of pickling frames through a pipe. `research/harness.py::_run_harness_parallel` runs whenever `max_workers > 1`.
  - `--phase` splits the job into `compute` (resumable, checkpointed per
    `--chunk-size`, default 25), `finalize` (cross-ticker cofire) and
    `harness`. Use the phases for anything long: `_chunk_already_done` keys
    on `(config_hash, chunk, of)`, so **keep `--chunk-size` identical across
    restarts** or every chunk re-runs.
  - **Each phase writes its own `runs` row** -- `backtest_compute` (one per
    chunk), `backtest_finalize`, `backtest_harness`. The harness writes no
    *event* rows, which is a different claim; it does record its run, and
    `notes` carries the result (`harness passed`, or the failing check).
    That is the cheapest way to watch a long run without polling processes.
  - **`compute` warns on every chunk that `cofire_count` is only correct
    within the chunk**, and excludes it from that write's update columns.
    That is by design: cofire is cross-sectional and a 25-ticker slice
    cannot see the other 1,445. `finalize` is the whole-universe pass that
    corrects it, and it is only correct if `compute` finished for the
    config.
  - **Superseded history.** 2026-08-13 measured 4h55m total (write 35m50s,
    harness ~4h19m) on 627,380 rows / 590 tickers under
    `697f3ae71428d392`, single-threaded harness. Kept only so an old
    `runs` row can be read correctly; do not quote it as an estimate.
  - **`runs` measures this from 2026-08-18, and did not before.** `cli.py::backtest` used to close its `with ingest.run_job(...)` block before calling `run_harness`, so `finished_at - started_at` timed the write phase **only** — 20-38 min against a 2h48m-to-4h55m job. A 2026-08-09 session read those durations as the whole job and briefly "corrected" this line to ~36 min. It was wrong. Session 15 moved the harness inside the block, so new rows time the whole job and a failing harness now records `status='failed'`. **Rows written before 2026-08-18 are still write-phase-only** and must be read that way; check `started_at` before quoting one.
  - `cscan weekly` genuinely is ~36 min: it calls `run_backtest` and deliberately skips the harness (`cli.py::weekly` docstring). Do not read a weekly duration as a `cscan backtest` duration.
- **`cscan nightly`, every step measured 2026-08-26** on 1,470 tickers, after
  the IPv6 fix. Twelve steps; the totals are dominated by three fetchers that
  ask one ticker at a time.

**Every figure below is one measured run, 2026-08-26 evening**, on 1,470
  tickers. Where a step has two regimes the normal one is given and the
  other is named, because quoting the wrong regime is how this table has
  been wrong three times.

  | step | wall clock | rows |
  |---|---|---|
  | `bars_daily` (batched) | 5.0 min | 5,740 |
  | `bars_hourly` (batched; loop fixed `4f97d8b`) | 5.4 min | 40,615 |
  | `actions` (batched + dated key `9b008c9`) | 4.0 min | 349 |
  | `market` | 0.0 min | 5 |
  | `shares` | 0.7 min | 236,008 |
  | `earnings` (bulk calendar `647ee25`) | **0.6 min** | 1,203 |
  | `indicators` (5-day, `max_workers=1`) | 1.4 min | 5,574 |
  | `events` (5-day) | 1.2 min | 3,119 |
  | `path_capture` (incremental) | 1.2 min | 17,117 |
  | `peak_labels` | 1.0 min | 484,929 |
  | `sync` (**incremental**, COPY) | **0.4 min** | 104,669 |
  | **total** | **~21 min** | |

  **Measured end to end 2026-08-26 19:00 PT: 20.5 minutes**, on 1,470
  tickers, against ~4h35m two days earlier. What changed, in order of size:

  | | before | after |
  |---|---|---|
  | `earnings` | 43.5 min | **0.6** — the Finnhub calendar is bulk and was being asked per symbol |
  | `sync` | 114.2 min | **0.4** — incremental bound, then `COPY` instead of a dict per row |
  | `bars_hourly` | 54.5 min | **5.4** — batched fetch, then three per-ticker scans removed |
  | `actions` | 21.3 min | **4.0** — batched, and the cache key now carries a date |

  - **This table has been wrong three separate ways, all on 2026-08-26.**
    Read the failure modes before trusting a figure in it.
    1. **The sum was never re-added.** `bars_hourly` was batched, the row
       was updated, the total stayed at ~4h35m. A session quoted it back as
       fact. **Re-add the column when you change a row.**
    2. **`actions` 21.3 min was a cache miss, and 0.3 min was the bug.**
       `fetch_actions` keyed on the bare ticker with no date, so it always
       hit and fetched nothing — 640 tickers had zero corporate actions
       after 2026-07-31. Neither number described a working step. See
       `BACKLOG.md`.
    3. **`path_capture` 41.2 min / 6.5M rows was a post-rebuild
       catch-up, not a nightly.** The `runs` history is unambiguous: normal
       nights are **1-3 min / 10-25k rows**, and the ~41 min runs follow a
       full backtest, which creates millions of events with incomplete
       forward windows. Throughput is *higher* on those (2,627/s against
       285/s), so the short runs are fixed overhead rather than slow work.
       Nothing is wrong with the step.

    The shared lesson: **a step has regimes, and one measurement is one
    regime.** Check `runs` for the distribution before recording a number
    here.
  - **Indicator chunking does not appear here and should not.** It
    fixed the full-history rebuild, where 1,462 tickers held 11 GB
    resident. Nightly's `indicators` step is a 5-day window at 0.4 min
    and never touched that path.
  - **`cscan sync` and nightly's sync are different commands now.** The
    bare command still copies everything; nightly passes
    `incremental=True`, bounded by the **serving store's own watermark** so
    a Pi that missed a night is caught up rather than left with a hole. A
    full pass is still ~7.4M rows -- run it after a rebuild or a reflash.
  - **`sync` writes via `COPY` into a TEMP staging table** (`bd8cbc5`).
    Measured 78,589 rows/s against ~1,090 for the dict path. Profiling said
    the obvious suspects were wrong: the Pi was 76% idle (load 1.11 of four
    cores, SD card 15% utilised) while the workstation held 894 MB and
    53.8% of *one* core. It was `to_dict("records")` and SQLAlchemy
    re-binding, not the network and not the database.
  - **`indicators` runs `max_workers=1` in nightly** (`cli.py`). Harmless at
    a 5-day window; do not read 1.4 min as what the step costs on history.
  - **`bars_hourly` at 5.4 min is `_back_adjust_hourly`, not fetching.**
    The fetch is ~15 batched requests, about 30 seconds. Predicting ~2 min
    was wrong and the remainder is real per-ticker computation.

**Editing a module while a job runs gives it a split brain.** Found
2026-08-26: `cscan nightly` imported `db_io` at startup, then imported
`sync` **lazily** when the sync step began -- picking up a `sync.py` edited
in between, which called a `db_io.copy_upsert` that the already-cached
`db_io` module did not have. It failed instantly with `module
'capitalscan.jobs.db_io' has no attribute 'copy_upsert'`.

Nothing was corrupted, and the failure was loud. But the rule is wider than
"no concurrent writers": **a long-running job holds whichever modules it has
already imported and picks up the rest from disk as it reaches them.** Edit
freely during a job whose remaining steps you are not touching; do not edit
a module the job has not reached yet.

  - **Three fetchers account for ~2 hours of that**, and only because they
    request one ticker at a time. `bars_hourly` was batched on 2026-08-26
    (54.5 min -> ~1 min); `actions` and `earnings` are not, and are in
    `BACKLOG.md`. `bars_daily` does the whole universe in 4.9 minutes by
    batching, which is the comparison that makes the point.
  - **Every per-step average recorded before 2026-08-26 was measured on ~929
    tickers.** The universe is now 1,470, so scale by ~1.6 before using an
    old figure -- `shares` went from a 2.2 min average to 10 minutes on
    exactly that change, and a stale threshold caused two false "this is
    stuck" alarms in one night.
- `cscan bars --daily --lookback 8000`: **11 minutes for 521 tickers /
  2,002,797 rows** (measured 2026-08-25). Do **not** extrapolate from a small
  sample: 10 tickers took 2m37s, which predicts 2h20m and is wrong by an
  order of magnitude, because `_batch_key` puts every ticker in one
  `yf.download` rather than one request each. Per-ticker timing does not
  scale here.
- `cscan indicators`: the slow one, and **it writes nothing until it
  finishes** -- results are collected across all tickers then upserted once.
  Querying `indicators` mid-run returns the pre-run count, which looks
  exactly like a hang. Two working runs were killed on 2026-08-25 for that
  reason. Pass `--workers 8`; it defaults to 1.
- `cscan bars --hourly --backfill`, all tickers: **~4.5-5.5 hours**. Yahoo caps hourly at 60 days per request, so backfill walks 13 sequential windows per ticker at 0.5 req/s. No incremental path — already-stored data does not reduce the cost.
- `cscan universe --quarter`, one quarter: **~18 seconds** on the
  1,563-ticker universe, so a full 66-quarter backfill is **~20 minutes**
  (measured 2026-08-26, all 66 quarters `ok`, zero failures).
  - This line has been wrong twice. It said ~10s, true at an earlier
    universe size. It was then "corrected" to ~2.6 min on 2026-08-25 --
    a figure taken while every `localhost` connect cost 130 seconds, so
    it measured the IPv6 bug rather than the job.
  - Use `scripts/universe_backfill.ps1` rather than a hand-rolled loop. It
    resumes (`-StartFrom 2017Q3`), times each quarter, and collects failures
    instead of dying on one bad quarter.
  - **Never run it while a backtest is running.** Not locking -- MVCC handles
    that -- but determinism: workers resolving eligibility against a
    `universe` that changes mid-run produce different output for one config,
    violating ADR 060.

**A `psql` session on the Pi reads the WRONG config generation.**
`run_sync` ends by pinning `capitalscan.default_config_hash` on the serving
database and **cannot**: custom `capitalscan.*` parameters need superuser,
and `capscan` is not one. The failure is logged and explicitly called
harmless, because `web/lib/db.ts` sets the hash on every connection from
`serving_config` (ADR 115) — so the *site* is always right.

What it is not harmless for is **you**, verifying by hand:

    psql on the Pi   current_setting(...)  ->  f66729c7eda212a4   (stale)
    serving_config                         ->  a38d3ca6b58295e8   (live)

Every serving view filters on that GUC, so a manual query silently reads a
generation the site does not serve. Hit 2026-08-26: `SELECT count(*) FROM
v_screen_live WHERE signal_date='2026-08-26'` returned **0** while the page
rendered 138 rows for that date. The instrument was wrong, not the system.

**Fixable, as of 2026-08-27.** `capscan` cannot set a custom parameter,
but `postgres` can, and SSH to the Pi gives you that:

```bash
ssh darischen@192.168.1.30 "sudo -u postgres psql -c   \"ALTER DATABASE capitalscan_serving SET capitalscan.default_config_hash = '<hash>'\""
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

Native Windows. The only Linux is inside the Postgres container, and that is transparent.

`ProcessPoolExecutor` uses **spawn**, not fork. Every job module must be importable with no side effects, every entry point needs `if __name__ == "__main__":`, and workers open their own database connections because connections are not picklable. Getting this wrong causes recursive process creation, which looks like a hang.

Scheduling is *intended* to be Windows Task Scheduler with catch-up enabled,
not cron or systemd. **Nothing is registered today.** Checked 2026-08-21:
`Get-ScheduledTask` returns no entry for `nightly`, `weekly` or the poller,
and every row in `runs` got there from a hand-run command. This line asserted
a live schedule until then, and that assertion was quoted back as fact in a
session before anyone checked it. Treat `nightly` and `weekly` as manual, and
`scripts/wait_and_poll.ps1` as something the user starts.

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
