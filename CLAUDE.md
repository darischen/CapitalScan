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

Read `docs/DECISIONS.md`. It holds 142 ADRs. They are decisions, not suggestions.

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
"C:\Program Files\Docker\Dockeresourcesin\docker.exe" ps -a
"C:\Program Files\Docker\Dockeresourcesin\docker.exe" start capitalscan-postgres
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

- `cscan backtest --workers 8`, full universe: **2h48m to 4h55m**. Write phase 20-36 min; the validation harness is single-threaded and takes the rest regardless of worker count — more workers do not shorten it.
  - **Re-measured 2026-08-13 by wall clock: 4h55m total** (write 35m50s, harness ~4h19m), on 627,380 rows / 590 tickers under `697f3ae71428d392`. That is 1.75x the 2h48m figure, so treat 2h48m as a floor rather than an estimate. Two plausible contributors, neither verified: the event count grew slightly, and ADR 108 added a seventh signal type, which widens `signal_types_all` and the cluster tagging the harness walks. **Budget five hours, not three.**
  - **`runs` measures this from 2026-08-18, and did not before.** `cli.py::backtest` used to close its `with ingest.run_job(...)` block before calling `run_harness`, so `finished_at - started_at` timed the write phase **only** — 20-38 min against a 2h48m-to-4h55m job. A 2026-08-09 session read those durations as the whole job and briefly "corrected" this line to ~36 min. It was wrong. Session 15 moved the harness inside the block, so new rows time the whole job and a failing harness now records `status='failed'`. **Rows written before 2026-08-18 are still write-phase-only** and must be read that way; check `started_at` before quoting one.
  - `cscan weekly` genuinely is ~36 min: it calls `run_backtest` and deliberately skips the harness (`cli.py::weekly` docstring). Do not read a weekly duration as a `cscan backtest` duration.
- `cscan bars --hourly --backfill`, all tickers: **~4.5-5.5 hours**. Yahoo caps hourly at 60 days per request, so backfill walks 13 sequential windows per ticker at 0.5 req/s. No incremental path — already-stored data does not reduce the cost.
- `cscan universe --quarter`, one quarter: ~10s.

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

Scheduling is Windows Task Scheduler with catch-up enabled, not cron or systemd.

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
