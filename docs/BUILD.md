# BUILD.md

Implementation plan for `CapitalScan`.

Read `DECISIONS.md` and `DESIGN.md` first. This document says **what to build in what order**; those say **why** and **what it is**.

Every task points at an ADR or a DESIGN section rather than restating the rule. One source of truth, no drift.

---

## 0. How this is organized

Work is grouped into **sessions**. A session is one coherent goal that fits in a single Claude Code context window and ends with a passing test. Session boundaries follow dependency seams, which is what keeps context switching low.

Sessions 0-7 deliver v1 (Phase 1). Sessions 8-9 deliver Phases 2-3. Session 10 is a data-layer session between the Phase 3 gate and Phase 4. Later phases are sketched at the end and get their own session plan once Phase 4 results exist.

| # | Session | Delivers | Exit test |
|---|---|---|---|
| 0 | Scaffold | Repo, tooling, Docker, CI, CLI skeleton | `cscan --help`; `pytest` passes on zero tests |
| 1 | Schema | All migrations, views, Alembic wired | `cscan db migrate` up and down on empty DB |
| 2 | Core: indicators | `config`, `types`, `indicators` + registry | Golden fixtures + external reference match |
| 3 | Core: signals & exits | `signals`, `exits`, `returns`, `costs`, `universe` | Property, parity, and look-ahead tests |
| 4 | Fetchers | `jobs/fetch/*` with retry, rate limit, cache | Integration test, 3 live tickers |
| 5 | Ingest jobs | bars, actions, market, shares, earnings, validation | `cscan backfill --tickers TSM,NVDA,AAPL --start 2020` |
| 6 | Compute jobs | indicators, universe, events, `scan()` | **Phase 1 gate** |
| 7 | Full backfill | Run it, inspect, fix | 750 tickers ingested, validation clean |
| 8 | Poller & notify | `poll`, Notifier, positions, call overlay | **Phase 2 gate** |
| 9 | Backtest engine | `research/backtest.py`, exit sweep | **Phase 3 gate — complete, passed** |
| 10 | Forward path store and derived label layer | Path table, reconciled label layer, new label families | Reconciliation against Session 9 labels passes with zero unexplained differences; tests and documentation complete |

Sessions 0-2 can run back to back in one sitting. Session 7 is mostly waiting.

**Session 9 is complete.** Phase 3 gate passed on all five criteria — see
`docs/RESULTS.md` (Phase 3 — Engine validation) for the run of record
(`run_id=backtest_20260802T183304_6b1c5b52`, `config_hash=3e598c59e7d71eae`)
and `.superpowers/sdd/2026-08-01-session-9-backtest/phase3-gate-measurement.md`
for the full measurement detail. The 18-config sweep and the entry-reuse
refactor decision remain open, tracked outside this document.

**Session 10 is complete.** Session 10 built the forward-path store and
derived label layer (tasks 10.1–10.6) and passes its gate with reconciliation
clean against Session 9 labels. ADR 094 records the design (path table as
source of truth, labels derived on demand). Tests and documentation are
complete (task 10.7). See `docs/sessions/session10.md` §4 for gate criteria and
`docs/RESULTS.md` (Session 10 tasks) for run details.

**Phase 4 boundary is after Session 10.** No change to signal detection, event
creation, or entry/exit logic. Phase 4 does not start until Session 10 passes,
which it now has.

**Label source contract for Phase 4** (pinned 2026-08-05, correcting an
earlier blanket statement here that the statistics layer "reads from the path
table" for everything — for forward returns, doing so would swap the price
series):

| Label family | Phase 4 reads | Why |
|---|---|---|
| `fwd_ret_1d/2d/3d/5d/10d` | `events.fwd_ret_*d` | Return measurement takes **total-return `adj_close`** (`core/returns.forward_returns`). `path.terminal` is split-adjusted close anchored to `entry_price` — a different quantity by design, which is why reconciliation marks the whole `fwd_ret_*d` family explained rather than matching it |
| MFE, MAE, time-to-MFE, capture_ratio | path-derived layer (`research/path_labels.py`) | Both sides use split-adjusted OHLC; reconciliation matches to zero |
| Reachability (`touched_*pct`, `day_touched_*pct`) at the configured targets | path-derived layer | Same series; residual is 9 verified boundary events |
| Any threshold or horizon beyond the materialized set | `research/path_queries.py` against `path` | Config-parametrized, no migration |

The rule underneath: **`path` is the source of truth for anything measured off
the traded price series, and never for return measurement across a horizon.**
DESIGN §2.2 and `core/returns.py`'s module docstring own that split; the path
table does not override it.

**Two consumer rules Phase 4 must follow** (added 2026-08-06):

**Filter on window completeness, always.** Every statistic reads only events
satisfying `fwd_window_days >= entry_offset + max_hold_days`. An event whose
forward window was still open when the backtest last ran carries labels frozen
at that moment, and `cscan weekly`'s refresh (`jobs/cli.py::weekly`) narrows
that staleness window without closing it — a signal that fired last night is
legitimately unlabelable until its window closes. The filter makes the
statistics correct regardless of when the refresh last ran, rather than
depending on scheduling. See ADR 094, "Materialized labels need a refresh job,
not just an exclusion filter."

**Never read `first_touch_day` alone.** `research/path_labels.py` exposes two
functions with deliberately different null semantics:

| Function | Returns `None` when |
|---|---|
| `touched_by` | the window is too short to answer — three-valued, so `True`/`False`/unknown are distinguishable |
| `first_touch_day` | **either** the target was never touched in a complete window **or** the window was too short |

Both are correct and both match §10.5's "null means untouched, never zero."
But a caller reading `first_touch_day` on its own cannot tell "did not happen"
from "cannot know yet," and averaging the two together silently biases the
time-to-touch distribution toward whatever the recent, still-accumulating
events look like. Pair the calls: use `touched_by` to establish
answerable-ness, then read `first_touch_day` for the day.

---

## 1. Ground rules for every session

1. **Read `DECISIONS.md` before writing code.** 87 ADRs. They are decisions, not suggestions. If a task appears to require contradicting one, stop and ask.
2. **Tests before implementation for anything in `core/`.** Coverage gate is 90% on `core/` only.
3. **No magic numbers outside `core/config.py`.**
4. **Every generated row carries `run_id` and `git_sha`.**
5. **Alembic tasks get step-by-step walkthroughs** — see §2 below. The user has not used Alembic before and this is a learning objective, not just a build step.
6. **Before writing any frontend component, read the frontend-design skill.**

---

## 2. Alembic protocol

The user is new to Alembic. Every task touching migrations must:

- Explain what the command does before running it
- Show the generated file and walk through each line
- Explain what `upgrade()` and `downgrade()` do in that specific case
- Show how to verify the result (`alembic current`, `\d tablename` in psql)
- Never run `--autogenerate` without reading the output aloud first

Expected usage over the project: ~15 migrations total, front-loaded into sessions 1-3. Day-to-day commands are `cscan db migrate` and `cscan db status`, wrapping `alembic upgrade head` and `alembic current`.

**Rule:** `cscan db migrate` applies to **both** databases by default and reports each. Single-target requires an explicit flag. Forgetting the second database is the main way this goes wrong.

---

## SESSION 0 — Scaffold

**Goal:** a repo that runs, tests, and lints, with a CLI that does nothing yet.

### Tasks

**0.1 Repo structure**

```
capitalscan/
  core/          config.py types.py indicators.py signals.py
                 exits.py returns.py costs.py universe.py
  jobs/          fetch/ ingest.py compute.py poll.py sync.py cli.py
  research/      backtest.py stats.py baseline.py benchmarks.py train.py
  handlers/      screen.py stats.py predict.py explain.py   # empty until Phase 5
  mcp/           server.py tools.py auth.py
  web/           (Next.js, session 10+)
  db/            migrations/ schema.sql seeds/
  tests/         unit/ property/ golden/ integration/ acceptance/
  data/          cache/ (gitignored)  universe_union.csv
  models/        (LFS, populated in Phase 6)
  logs/          (gitignored)
  docs/          DECISIONS.md DESIGN.md BUILD.md TESTS.md RESULTS.md
  scripts/       wip_snapshot.ps1 backfill.bat tasks/*.xml
  CLAUDE.md
  pyproject.toml
  docker-compose.yml
  .env.example
```

`handlers/`, `mcp/`, and `web/` are created empty in session 0 and stay empty until Phase 5. They exist in the scaffold so imports and tooling config are settled once.

**0.2 `pyproject.toml`**

Dependencies: `pandas`, `numpy`, `scipy`, `sqlalchemy`, `psycopg[binary]`, `alembic`, `pydantic`, `typer`, `rich`, `yfinance`, `pandas-market-calendars`, `requests`, `pyarrow`, `python-dotenv`, `tomli`.

Dev: `pytest`, `pytest-xdist`, `pytest-cov`, `hypothesis`, `testcontainers[postgres]`, `ruff`, `mypy`.

Deferred to later sessions: `lightgbm`, `scikit-learn` (Phase 6), `mcp` (Phase 5).

Use `uv`. Commit the lockfile.

**0.3 `docker-compose.yml`**

Docker Desktop on Windows. This is the only Linux in the local stack (ADR 081) and it is transparent.

Postgres 16, named volume, port 5432. Tuning per DESIGN §9.3:

```
shared_buffers=1GB
work_mem=32MB
maintenance_work_mem=512MB
effective_cache_size=4GB
max_parallel_workers=8
```

Default Postgres settings make the backfill noticeably slower than necessary.

**0.4 `.env.example`**

```
DATABASE_URL_RESEARCH=postgresql://capscan:capscan@localhost:5432/capitalscan
DATABASE_URL_SERVING=
SEC_USER_AGENT="Your Name your@email.com"
FINNHUB_API_KEY=
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-6
NOTIFY_DISCORD_WEBHOOK=
NOTIFY_NTFY_TOPIC=
NOTIFY_SMTP_HOST=
NOTIFY_SMTP_USER=
NOTIFY_SMTP_PASS=
NOTIFY_SMTP_TO=
MCP_BEARER_TOKEN=
```

`SEC_USER_AGENT` must contain a real email. SEC blocks requests without it at the IP level, persistently.

**0.4b Windows multiprocessing guard**

`ProcessPoolExecutor` uses **spawn** on Windows. Set this up correctly once in session 0 rather than debugging it in session 6:

- Every job module importable with no side effects. No top-level execution.
- `if __name__ == "__main__":` on every entry point. Without it, spawn recursively creates processes.
- Workers open their own database connections. Connections are not picklable.

Add a test in session 0 that spawns a trivial pool and asserts it returns, so the guard is verified before any real parallel work exists.

**0.5 Config resolution — in `jobs/`, not `core/`**

`core/config.py` holds frozen dataclasses only. Its sole import is `dataclasses`. No `open()`, no `os.getenv()`, no `Path.exists()`, no `dotenv`. Invariant 1 applies to `core/config.py` like every other module in `core/`.

`jobs/config.py` owns resolution: CLI flag > env var > `config.toml` > dataclass default. It returns populated frozen dataclasses. `core/` receives them as arguments.

The resolver raises `ConfigError` on parse failure, unknown keys, and type-coercion failure. Never fall back to defaults on malformed input: `config_hash` is stamped on every generated row, so a silently-defaulted config produces provenance that describes parameters which never ran.

Tests for the resolver must pass an explicit `config_file` and clear `CAPSCAN_*` with `monkeypatch.delenv`. A resolver reading ambient CWD and environment makes the suite depend on the machine, and a green CI then proves only that no stray variable happened to be set.

**0.6 CLI skeleton**

Typer app named `cscan`. Stub commands with `--help` text for every job in DESIGN §4.1. Each stub raises `NotImplementedError`.

**0.7 Logging**

Rotating JSON lines to `logs/`, plus stdout. `cscan logs --job <name> --tail N`.

**0.8 CI**

`.github/workflows/ci.yml`:
- fast job: `ruff check`, `ruff format --check`, `mypy`, `pytest tests/unit tests/property`
- slow job: `pytest tests/golden tests/integration` with a Postgres service container

**0.9 `.gitignore` and WIP script**

Gitignore: `data/cache/`, `logs/`, `.env.local`, `__pycache__`, `.venv`.

`scripts/wip_snapshot.ps1` per ADR 082 — **explicit path allowlist, never `git add -A`**, pushes to a throwaway `wip` branch.

**0.10 Git LFS**

Set up **now**, before any binary is committed. Adding LFS after the fact requires rewriting history.

```bash
git lfs install
git lfs track "models/**/*.txt"       # LightGBM native format
git lfs track "models/**/*.pkl"       # isotonic calibrators
git lfs track "models/**/*.parquet"   # training feature snapshots
git add .gitattributes && git commit -m "configure git lfs"
```

Per ADR 082, LFS holds **promoted model artifacts only** — roughly 22 MB per version, monthly. Free tier is 1 GB storage and 1 GB monthly bandwidth, which covers several years at that rate.

Explicitly **not** in LFS: the research database (1.5 GB, goes to GitHub Release assets monthly per ADR 083), the raw fetch cache (refetchable, gitignored), and logs.

Verify with `git lfs track` and confirm `.gitattributes` is committed.

### Acceptance

```powershell
docker compose up -d ; uv sync
cscan --help                # lists all commands
pytest                      # spawn-guard test passes, exits 0
ruff check ; mypy core/     # clean
```

---

## SESSION 1 — Schema

**Goal:** every table and view from DESIGN §2.5, §5.7, §6.8, §7.8, §8.5 exists, with Alembic managing it.

### Tasks

**1.1 Alembic init** *(walkthrough required, see §2)*

```bash
alembic init db/migrations
```

Edit `alembic.ini` and `env.py` to read `DATABASE_URL_RESEARCH` from env. Explain what each file does.

**1.2 Migration 001: reference tables**

`trading_days`, `tickers`, `corporate_actions`, `shares_outstanding`, `earnings`. DDL is in DESIGN §2.5 — copy it exactly.

**1.3 Migration 002: market data**

`bars`, `bar_rejects`, `market_days`, `indicators`. Note the CHECK constraints on `bars`; they are the first line of validation.

**1.4 Migration 003: universe and audit**

`universe`, `runs`, `scheduled_runs`, `poller_sessions`, `quotes_live`, `signal_reports`.

Note: there is no `regime` table. `universe` already stores `as_of` plus the five `crit_*` columns separately, which is what ADR 014 requires for ablation. Do not create a second one.

**1.5 Migration 004: events**

**`events` has no `cell_id` column and must not gain one.** `cell_id` is derived, not stored — see DESIGN §8.2. If a view appears to need it, join on component columns instead.



`events` per DESIGN §5.7, with all three indexes. This is the widest table; take care with the cluster columns and the UNIQUE constraint on `(config_hash, ticker, signal_date, signal_type, entry_kind)`.

**1.6 Migration 005: statistics, predictions, positions**

`cell_stats` (DESIGN §6.9), `benchmarks` (§6.10), `predictions` and `outcomes` (§7.8), `positions` and `order_intents` (§8.5).

`signal_reports` has a foreign key to `predictions`, so if migration 003 created it, add that constraint here rather than earlier.

**1.7 Migration 006: views**

Create `cell_key()` first — it is an immutable SQL function the stats job and the views both use.

All eight views, with full DDL in DESIGN §8.2: `v_screen`, `v_ticker_state`, `v_chart`, `v_stats`, `v_events`, `v_forward`, `v_universe`, `v_positions`. Copy them exactly.

`v_positions` depends on `v_ticker_state`, so create it after. `v_screen` hardcodes `split_key = 'validate'` in its join. This is deliberate and must not be changed to inherit `e.split_key`, which would leak holdout statistics into the UI.

`v_events` uses `current_setting('capitalscan.default_config_hash', true)` — the `true` second argument makes it return NULL rather than erroring when unset, which is what you want before the first backtest exists.

**1.8 CLI wiring**

```
cscan db migrate     # alembic upgrade head, BOTH databases by default
cscan db status      # alembic current for each
cscan db rollback    # alembic downgrade -1, with confirmation prompt
cscan db schema      # pg_dump --schema-only > db/schema.sql
```

**1.9 Commit `db/schema.sql`**

Regenerated after every migration. 30-50 KB of readable DDL, far better as a reference than reading migration files.

### Acceptance

```bash
cscan db migrate                    # clean apply on empty database
cscan db status                     # reports head revision
cscan db rollback && cscan db migrate  # down and back up, no errors
psql -c '\dt' | wc -l            # all tables present
psql -c '\dv'                    # all views present
```

---

## SESSION 2 — Core: indicators

**Goal:** correct, verified indicator computation with zero IO.

### Tasks

**2.1 `core/config.py`**

Every dataclass from DESIGN §3.3, frozen, with the defaults exactly as written: `IndicatorParams`, `SignalParams`, `ExitParams`, `CostParams`, `UniverseParams`, `StatsParams`, `SplitParams`.

`StatsParams` holds the suppression threshold (`min_n_eff = 30`), FDR alpha, replication counts, and bucket boundaries. These are used in Phase 4 but belong here now so they are never written as literals elsewhere.

**2.2 `core/types.py`**

Every enum and dataclass from DESIGN §3.4.

**2.3 Indicator registry** *(ADR 051)*

```python
@register("name", deps=[...], warmup=N, dtype="numeric(12,6)")
```

Registry exposes `max_warmup()` for the read-window expansion in the indicators job.

**2.4 Indicator functions**

`bollinger`, `stochastic` (fast and full), `atr`, `realized_vol`, `rolling_percentile`, `drawdown_from_high`, `sma_slope`, `volume_zscore`, plus `k_cross_up` / `k_cross_down`.

Formulas are pinned in DESIGN §3.5. Do not derive them from another source.

Three specifics that decide correctness — all three are in DESIGN §3.5 and each needs a test:
- Which price series each function takes (and the `realized_vol` exception)
- Stochastic returns NaN when `H_14 == L_14`, never 50 or 0
- `rolling_percentile` convention: fraction of the trailing window at or below, strict trailing

**2.5 `compute_all`**

Iterates the registry. One ticker in, one row per bar out, columns matching the `indicators` table exactly. No translation layer.

**2.6 Golden fixtures**

Build the fixtures listed in `TESTS.md` §4. At minimum for this session: `tsm_2026_07.csv`, `nvda_split_2024.csv`, `flat_series.csv`.

**2.7 External verification tooling** *(ADR 086)*

```bash
cscan verify-indicators --ticker TSM --ticker NVDA --dates 2026-07-29,...
```

Prints computed `bb_upper`, `bb_mid`, `bb_lower`, `k_full`, `d_full` and writes `tests/golden/external_reference.csv` with empty `external_*` columns. The user fills these once by hand from StockCharts or TradingView (~30 min). A test then asserts agreement within 0.1%.

This catches the ddof and SMA-vs-EMA class of silent divergence that every downstream number would inherit.

### Acceptance

```bash
pytest tests/unit/test_indicators.py tests/golden/ -x
# external_reference test passes once the user fills the CSV
```

---

## SESSION 3 — Core: signals and exits

**Goal:** the two modules where a bug produces a plausible-looking wrong number.

### Tasks

**3.1 `core/signals.py`**

`_breach`, `detect`, `breach_live` per DESIGN §3.6. Implement `_breach` first; everything routes through it.

Confluence definitions are pinned in DESIGN §3.6. **Indicators are read at t−1, never t.** This is the highest-risk silent failure in the system and it gets a dedicated test.

Multi-signal handling per ADR 057: one hit, most-specific `signal_type`, full `signal_types_all`, `signal_strength` count. Specificity ranking is fixed in DESIGN §3.6.

Crossover flags computed and stored, never gating (ADR 045).

**3.2 `core/exits.py`**

`resolve_exit` with the **exact** priority order in DESIGN §5.5. Gap rules are in the table there. Do not invent an ordering.

The four details in §5.5 each need a test: gap checks first, band levels from i−1, `ambiguous` on stop-and-target collision, stochastic evaluated last.

**3.3 `core/returns.py`**

`forward_returns`, `mfe_mae`, `realized_return`, `entry_price_for`.

Note the coverage constraint in DESIGN §3.8: `TOUCH_5M` and `TOUCH_30M` return null before the hourly archive begins, never a fabricated value.

**3.4 `core/costs.py`**

`apply_costs`, `borrow_cost`, `slippage` per DESIGN §3.9.

**3.5 `core/universe.py`**

`evaluate_criteria` returning the five ADR 014 criteria **separately**; `is_tradeable` accepting a subset, so ablation is a config change.

**3.6 Property tests**

Exit invariants across 10,000 hypothesis-generated cases. The four invariants are in `TESTS.md` §3.4. The sharp one: `mfe >= realized_return`. A violation means path metrics and the exit disagree about what happened.

**3.7 Parity test** *(ADR 006 enforcement)*

`detect()` on a bar must produce the same signal set as `breach_live()` walked across a simulated intraday path (open → low → high → close).

**3.8 Look-ahead test**

Shift all indicators forward one bar, rerun detection, assert Jaccard overlap below 0.5. High overlap means the signal is not reading the indicators or is already using future data.

### Acceptance

```bash
pytest tests/unit/test_signals.py tests/unit/test_exits.py \
       tests/property/ -x
# parity, look-ahead, and all four exit invariants pass
```

---

## SESSION 4 — Fetchers

**Goal:** all external IO behind one contract with retry, rate limiting, and disk caching.

### Tasks

**4.1 `jobs/fetch/base.py`**

`Fetcher` protocol, `with_retry`, `rate_limited`, `cached` per DESIGN §4.2.

Retry on connection errors, timeouts, 429, 5xx. **Never retry 404** — that means the ticker does not exist and belongs in `bar_rejects`.

**4.2 Disk cache**

`data/cache/{source}/{key}.parquet`, written before parsing. This is what turns a 40-minute backfill into a 90-second replay while iterating on the parser, which will happen several times.

**4.3 Rate limits**

| Source | Setting |
|---|---|
| yfinance | 0.5 req/s, batches of 50 |
| SEC EDGAR | 8 req/s, `User-Agent` mandatory |
| Finnhub | 0.8 req/s |

Removed 2026-08-01: a Stooq row (2 req/s) was here for the cross-check fetcher. Stooq began serving a JavaScript proof-of-work challenge to automated requests on every endpoint tried, so the fetcher and the cross-check it backed were both removed; the pipeline is single-source on Yahoo.

**4.4 Fetchers**

`yahoo.py` (daily bars, hourly bars, quotes, actions, chains), `sec.py` (XBRL company facts, 8-K submissions), `finnhub.py` (forward calendar), `wikipedia.py` (membership history). (`stooq.py` was removed 2026-08-01; see §4.3.)

**4.5 Progress reporting** *(ADR 052)*

`rich.progress` for anything over 30 seconds: current item, completed/total, elapsed, ETA, running error count. `--quiet` emits JSON lines for cron.

### Acceptance

```bash
pytest tests/integration/test_fetchers.py
# fetches 3 real tickers, cache hit on second call, rate limit respected
```

---

## SESSION 5 — Ingest jobs

**Goal:** raw data landing correctly, with validation that fails loudly.

### Tasks

**5.1 `calendar`** — NYSE calendar into `trading_days`.

**5.2 `tickers` and `membership`**

Wikipedia scrape, manual review, freeze to `data/universe_union.csv` and commit it (ADR 055). Roughly 600-750 tickers with add and remove dates. CIK mapping from SEC.

**5.3 `bars_daily`**

Per DESIGN §4.3. `auto_adjust=False` is **mandatory** — the default destroys the split-adjusted series the band levels need. `threads=False`.

Handle both failure modes explicitly: partial batch failure (NaN columns, no exception) and silent truncation (short history returned without warning).

**5.4 `bars_hourly`**

Per DESIGN §4.4. 13 sequential 60-day windows per ticker. Regular session only. Checkpoint per ticker.

**5.5 `actions`, `market`**

Splits and dividends into `corporate_actions`; SPX and VIX into `market_days`.

**5.6 `shares`**

SEC XBRL company facts. Point-in-time semantics per DESIGN §2.4: use the latest filing with `filed_on < as_of`.

**5.7 `earnings`**

Historical from EDGAR 8-K filings, frozen after backfill. Forward from Finnhub, 90-day window, weekly (ADR 036). Store `source` and `confidence` per row.

**5.8 Validation**

Every rule in DESIGN §2.3, writing to `bar_rejects` rather than dropping silently.

`cscan validate --report` prints reject counts by rule and per-ticker coverage. This is a deliberate human gate in the backfill runbook. (Until 2026-08-01 it also ran a Stooq cross-check on 20 tickers; removed when the vendor began blocking automated requests — see §4.3.)

**5.9 Idempotency**

All writes are upserts. Verify by running any ingest job twice and asserting identical row counts.

### Acceptance

```bash
cscan backfill --tickers TSM,NVDA,AAPL --start 2020-01-01 --through-validate
cscan validate --report      # zero rejects at 'reject' severity
# rerun the same command: row counts unchanged
```

---

## SESSION 6 — Compute jobs and `scan()`

**Goal:** the v1 deliverable.

### Tasks

**6.1 `indicators` job**

Per DESIGN §4.5. **The read window and write window differ by design.** `read_start = target_start − ceil(max_warmup() × 1.6) days`. Getting this wrong produces silent nulls at the range boundary.

`ProcessPoolExecutor(max_workers=14)`. Merge `days_to_earnings`, `spx_ret_1d`, and VIX in a single vectorized pass after per-ticker computation.

**6.2 `universe` job**

Quarterly evaluation per DESIGN §4.6. Handle all three data hazards: shares lag, sector median fallback for thin sectors, revenue growth null-vs-false distinction.

**6.3 `events` job**

Per DESIGN §4.7. The t−1 indicator read is guarded **again** here at the job level — two layers, deliberately.

Debounce: one event per ticker per bound per day. Cluster tagging per DESIGN §5.3.

**6.4 `scan()`** *(ADR 049 — the v1 acceptance criterion)*

```python
scan(tickers, signal_types, start, end, universe="trade") -> pd.DataFrame
```

One row per event: ticker, timestamp, signal type, touch level, %B, %K full, %K fast, crossover flags, drawdown bucket, trend state. No statistics, no predictions, no exits.

**6.5 CLI**

```bash
cscan scan --ticker TSM --start 2026-07-01 --end 2026-07-30
cscan scan --universe trade --date today
```

**6.6 Warmup assertion** *(ADR 040)*

Test asserting zero nulls in every required indicator column on or after 2010-01-01 for any ticker with continuous coverage.

### Acceptance — **PHASE 1 GATE**

```bash
cscan scan --ticker TSM --start 2026-07-01 --end 2026-07-30
# returns the 2026-07-29 event with correct %B and %K
```

Plus:
- All golden fixtures pass
- Zero nulls in indicators after 2010-01-01
- Random-walk null test passes (`TESTS.md` §6)

(This gate used to also require Stooq agreement within tolerance. Removed 2026-08-01 — Stooq began blocking automated requests; the pipeline is single-source on Yahoo.)

---

## SESSION 7 — Full backfill

**Goal:** 750 tickers, 16 years, clean.

### Tasks

**7.1 Run the backfill**

```bash
cscan backfill --all --through-validate   # ~50 min, stops at the gate
# read the report
cscan backfill --all --resume             # ~2.5 hr
cscan bars --hourly --backfill            # ~5.4 hr, overnight, checkpointed
```

**7.2 Inspect and fix**

Expect failures on delisted tickers, ADRs with unusual histories, and pre-2012 data quality. Every fix goes into a validation rule, not a special case.

**7.3 Record the result**

Append to `RESULTS.md`: ticker count, bar count, reject counts by rule, coverage gaps, and any tickers dropped with reasons.

**7.4 First `pg_dump`**

Nightly dump to the second local disk. Monthly to a GitHub Release asset (~300 MB compressed).

### Acceptance

- 750 tickers with `first_bar` and `last_bar` populated
- `cscan validate --report` clean at `reject` severity
- Indicators computed for the full range with no post-2010 nulls
- `cscan scan --universe trade --date <any recent date>` returns plausible results

---

## SESSION 8 — Poller and notifications

**Goal:** Phase 2.

### Tasks

**8.1 `poll` job** — DESIGN §4.8. Bands loaded once at 09:15; the loop is two float comparisons per ticker per tick.

**8.2 Debounce state in Postgres**, not process memory, so a restart mid-session does not re-fire.

**8.3 `Notifier` protocol** *(ADR 054)* — SMTP, Discord webhook, ntfy. Each independently toggleable, any combination active.

**8.4 `positions`** — user-declared entry, closed positions with realized returns, exit signals pushed (ADR 073).

**8.5 `order_intents`** *(ADR 048)* — structured intent with an idempotency key. Renders as a notification. **No broker client. No credentials. No code path capable of placing an order.**

**8.6 Live call overlay** *(ADR 050)* — on a long signal, pull the current chain from yfinance and show cost at the real ask, breakeven, payoff at each reachability rung, and max loss. Labeled as priced from live quotes, explicitly not backtested.

**8.7 `poller_sessions`** — coverage logging so gaps are visible rather than inferred (ADR 084).

**8.8 Task Scheduler** *(ADR 080)* — enable "Run task as soon as possible after a scheduled start is missed" so a missed job fires at next boot. Export task definitions to `scripts/tasks/*.xml` and commit them. `scheduled_runs` records delay.

### Acceptance — **PHASE 2 GATE**

- Poller detects a live breach within one polling interval
- Notification delivered on all three configured channels
- `poller_sessions` records the session with coverage percentage
- Restart mid-session does not re-fire an already-sent event

---

## SESSION 9 — Backtest engine

**Goal:** Phase 3. **Complete — Phase 3 gate passed 2026-08-02.** See
`docs/RESULTS.md` for the run of record and the gate table.

### Tasks

**9.0 Wire the hourly pull into the nightly chain** *(prerequisite for 9.2)* — `run_bars_hourly` has exactly one caller, the `bars` CLI command. `nightly()` in `jobs/cli.py` fetches daily bars and skips hourly, so nothing keeps the hourly table current and it freezes at whatever the last manual `cscan bars --hourly --backfill` produced. Add one line after `run_bars_daily`:

```python
ingest.run_bars_hourly(tickers, start, end, engine=engine)
```

`start` is already `end - 5` in that function, so `_hourly_windows` yields a single 60-day window per ticker rather than the 13 a full backfill walks. Cost is roughly **21 minutes** for ~630 tickers at `RATE_LIMIT_PER_SEC = 0.5`, against ~4.6 hours for the 725-day backfill.

Safe to defer until this session: hourly is inert until 9.2 exists, since DESIGN §3.8 reads it only to resolve `TOUCH_5M` and `TOUCH_30M`. Bars already written persist, and only the *fetchable* window slides, so a later run recovers any gap shorter than 725 days.

Failure mode if skipped: `entry_price_for` returns null rather than raising when hourly is absent (`tests/unit/test_returns.py:199`), so a stale hourly table silently drops two of the four entry kinds instead of erroring.

**9.1 `research/backtest.py`** — the pipeline in DESIGN §5.2, exactly in that order.

**9.2 Entry resolution** — four kinds, DESIGN §5.4, with the gap rule.

**9.3 Exit resolution** — calls `core.exits.resolve_exit`. No second implementation.

**9.4 Path metrics** — MFE, MAE, time-to-MFE over `[t+1, exit_idx]`; reachability over the **full** `[t+1, t+5]` regardless of exit timing.

**9.5 Context tagging and split assignment** — `split_key` assigned at creation by date, never at query time.

**9.6 `cofire_count` post-pass** — single-threaded, groups across tickers.

**9.7 Determinism** *(ADR 060)* — sorted dispatch, sorted write, no wall-clock reads, `config_hash` on every row.

**9.8 Validation harness** — the five checks in DESIGN §5.10.

**9.9 Default config run first** *(ADR 059)* — ATR stop k=1.5, target 4%, `NEXT_OPEN`. Full harness. **Then ~20 events inspected by hand against charts before any sweep.**

**9.10 Sweep** — 18 exit configs, entry prices computed once and reused. ~4-5 minutes.

### Acceptance — **PHASE 3 GATE**

- Exit invariants hold across 10,000 property-generated cases
- Ambiguity rate below 10%, or hourly escalation implemented
- Event rate passes the three checks in §9a below
- Two runs with identical config produce identical output ignoring `run_id`
- All five validation harness checks pass

### 9a. Event-rate criterion

The old wording — "within 20% of the analytical estimate (~4% of ticker-days
for confluence)" — was **ambiguous and its estimate was wrong**. Measured
2026-08-01 over 2,371,529 ticker-days from 2010-01-01, joining bars to
indicators at t−1 (the pairing `detect` actually uses):

| Reading of "confluence" | Rate |
|---|---|
| `confluence_low`, on closes | 4.43% |
| `confluence_low`, on intraday extremes | 7.16% |
| either side, on closes | 11.38% |
| either side, on intraday extremes | **18.34%** |

Three readings, four-fold spread. The one matching the old ~4% estimate is
`confluence_low` on **closes** — which is exactly what ADR 005 says not to
do, since "a daily bar's extremes are the intraday touch". The engine's
18.34% is the deliberate design: intraday extremes, both sides.

**Do not replace the estimate with the measured value.** A criterion set to
what the engine currently emits is circular — it passes on the day it is
written and every day after, including the days something breaks. An
analytical estimate earns its place by being derived independently.

Three checks instead, each verifiable against something other than itself:

**1. Structural invariants.** Provable, and a violation means detection is
genuinely broken:

```
P(low  <= bb_lower)  >=  P(close <= bb_lower)     (low <= close, always)
P(high >= bb_upper)  >=  P(close >= bb_upper)
P(confluence_low)    <=  min(P(lower touch), P(oversold))
```

**2. Component rates against theory.** Each has an independent prediction,
so a drift is interpretable rather than just different:

| Component | Independent prediction | Measured 2026-08-01 |
|---|---|---|
| `P(k_full <= 20)` | ~20%: the oscillator is bounded [0,100] and roughly uniform | 15.92% |
| `P(close <= bb_lower)` | ~2.5% under normality; higher with fat tails | 5.29% |
| `P(low <= bb_lower)` | strictly above the close rate | 11.18% |

**3. Headline band, deliberately wide.** Confluence on either side, intraday
extremes, fires on **10-25%** of ticker-days. Wide enough not to be
circular; tight enough to catch an order-of-magnitude error such as a
dropped t−1 shift.

Whether 18% is a *useful* event rate is a Phase 4 statistical-power
question, not a Phase 3 correctness one.

---

## Later phases

Session plans get written once the preceding gate produces data.

**Phase 4 — Statistics.** Baselines (dual, per ticker-year), effective sample size, three benchmark arms with the 200-replication randomization null, **trim-and-redeploy variant (DESIGN §6.5, ADR 017)**, DCA comparison, headline grid, BH correction, era breakdown, cluster-sequence comparison, long-vs-short asymmetry, **pre/post-tax reporting (DESIGN §8.8, ADR 032)**, `cell_stats`. Plus the statistical self-validation in DESIGN §6.12 — **the random-walk null test runs before any real result is trusted.**

**Phase 5 — Frontend, tools, MCP.** Screener, ticker page, research page, `handlers/` layer, seven tools, response validator, MCP server (ADR 027, built after Phase 4 results exist).

**Phase 6 — Model.** Eleven heads, purged walk-forward CV, isotonic calibration, four-check promotion gate, forward log, live inference in the poller, breach-depth features added after the base model exists.

**Phase 7 — Options.** Synthetic Black-Scholes layer labeled synthetic, then one month of Polygon chains to measure and publish the pricing error.

---

## Kill criteria

Fixed in advance (ADR 033), before sunk cost exists.

- **No cell beats its per-ticker-year baseline** by the power-adjusted threshold at sufficient `n_eff` after FDR correction → the two-indicator hypothesis is dead as a standalone claim.
- **Validation edge under half the training edge** → overfit. Cut features, widen cells.
- **Holdout edge negative** → report it.

What survives a null result: the event-study engine with correct look-ahead handling and MFE/MAE tracking, the tool-restricted chat layer, the calibration methodology, and the ingest and scheduling pipeline. The pivot keeps the engine and swaps the input signal — volatility term structure, earnings drift, cross-sectional momentum residuals, and volume-price divergence are all testable in the same framework with no rewrite.

**Holdout is evaluated exactly once, at the end, and published whatever it says.**
