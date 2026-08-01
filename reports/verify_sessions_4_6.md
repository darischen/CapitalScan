# Verification: Sessions 4-6 (Fetchers, Ingest, Compute)

Date: 2026-08-01
Scope: BUILD.md Sessions 4, 5, 6. Read-only audit. `pytest` run only on `capitalscan/tests/unit` and `capitalscan/tests/property` (both DB-free). No integration test, no `cscan` command, no non-SELECT SQL, and no file edits were executed, per the standing safety constraint (a 4.6-hour ingest job is running against the live Postgres instance).

Test results actually observed:

```
uv run pytest capitalscan/tests/unit      -> 391 passed in 2.90s
uv run pytest capitalscan/tests/property  -> 20 passed in 23.38s
```

`capitalscan/tests/integration/*` (`test_fetchers.py`, `test_ingest.py`, `test_poll.py`, `test_positions.py`) were **read but not executed**. They are well-targeted at the BUILD.md acceptance criteria (mocked network boundary, real Postgres via a truncate/re-truncate fixture) but their pass/fail status is unverified here.

---

## SESSION 4 — Fetchers: **PASS** (code-reviewed; integration test unexecuted)

| Criterion | Status | Evidence |
|---|---|---|
| `Fetcher` protocol, `with_retry`, `rate_limited`, `cached` (DESIGN §4.2) | PASS | `capitalscan/jobs/fetch/base.py:28-177` — protocol, exponential backoff 2/4/8/16s with jitter, token-bucket-style rate limiter, parquet disk cache, all matching spec |
| Never retry 404 | PASS | `base.py:49-57` (`_is_retryable`) explicitly excludes `NotFoundError` and HTTP 404 from the retryable set; `sec.py:44-49`, `stooq.py:35-36`, `finnhub.py:38-39` all raise `NotFoundError` on 404 rather than looping |
| Disk cache at `data/cache/{source}/{key}.parquet` | PASS | `base.py:138-177` (`cache_path`, `cached`); tests confirm cache-hit behavior (`test_second_call_is_a_cache_hit`) |
| Rate limits per source (yfinance 0.5, SEC 8, Finnhub 0.8, Stooq 2) | PASS | `yahoo.py:18` (`RATE_LIMIT_PER_SEC = 0.5`), `sec.py:20` (`8.0`), `finnhub.py:19` (`0.8`), `stooq.py:18` (`2.0`) — all match DESIGN §4.2's table exactly |
| Fetchers: yahoo (daily, hourly, quotes, actions, chains), sec, finnhub, stooq, wikipedia | PASS | All five files present and implement the documented surface: `yahoo.py` has `fetch_bars_daily`, `fetch_bars_hourly`, `fetch_actions`, `fetch_quotes`, `fetch_option_expirations`, `fetch_chain`; `sec.py` has CIK lookup, XBRL company facts, 8-K dates; `finnhub.py` has forward calendar; `stooq.py` has daily cross-check; `wikipedia.py` has current constituents + membership changes |
| Progress reporting for jobs >30s | PARTIAL | Implemented for `run_bars_hourly` (`rich.progress`, `ingest.py:478-509`) and the fetch layer generally relies on the ingest layer for this — session 5 code, not session 4 code, actually carries it. No `--quiet` JSON-lines mode found anywhere. |
| Acceptance: fetch 3 real tickers, cache hit on 2nd call, rate limit respected | PARTIAL/UNVERIFIED | `tests/integration/test_fetchers.py` covers exactly this (mocked, not live) — read and looks correct, but not executed under the safety constraint. It is **not literally 3 live tickers** as BUILD.md's acceptance line states; it is 3 tickers against a mocked `yf.download`. That is a defensible interpretation (deterministic CI) but is a documented deviation from the literal acceptance text, not something BUILD.md flags as intentional. |

**Gaps/concerns — Session 4:**
- `capitalscan/jobs/fetch/base.py` has no `--quiet` / JSON-lines progress mode; DESIGN §4.10 assigns that to jobs generally, so this is arguably session 5/6 scope, not a session 4 gap, but nothing anywhere in the codebase implements it yet.
- The "3 live tickers" acceptance line in BUILD.md is satisfied only by a mocked substitute test. Reasonable engineering choice, but worth the user's explicit sign-off since it silently reinterprets the acceptance criterion.

---

## SESSION 5 — Ingest jobs: **PARTIAL**

| Criterion | Status | Evidence |
|---|---|---|
| `calendar` → `trading_days` | PASS | `ingest.py:112-127` (`run_calendar`), uses `pandas_market_calendars` per DESIGN §2.3 |
| `tickers` / `membership`, freeze to `data/universe_union.csv` | PASS | `ingest.py:133-320`; `UniverseFrozenError` guard against re-running over a reviewed file (`is_reviewed`, `run_membership`) implements ADR 055 correctly |
| `bars_daily`, `auto_adjust=False` mandatory, `threads=False` | PASS | `yahoo.py:57-69` — both flags set exactly as DESIGN §4.3 requires, with a comment explaining why |
| `bars_daily` handles partial-batch failure and silent truncation | PASS | `yahoo.py:110-131` (`_fetch_daily_batch`) retries only the failed tickers individually; `ingest.py:528-551` (`_update_ticker_coverage`) records `first_bar`/`last_bar` per DESIGN §4.3's silent-truncation note |
| `bars_hourly`, 13 windows, regular session only, checkpointed per ticker | PASS | `yahoo.py:154-165` (`_hourly_windows`, 725-day cap, 60-day windows), `yahoo.py:196-210` (session filter `between_time("09:30","16:00")`), `ingest.py:465-512` (`run_bars_hourly`) checkpoints via per-ticker upsert inside the loop — matches BUILD's "modified earlier today" note and has a dedicated unit test (`test_bars_hourly_checkpoint.py`, passing) |
| `actions`, `market` | PASS | `ingest.py:557-619` |
| `shares`, point-in-time semantics | PASS | `ingest.py:625-660`; point-in-time selection (`filed_on < as_of`) is correctly deferred to the *query* side (`compute.py:252-264`, `_latest_shares`), matching DESIGN §2.4/§4.6 exactly — the ingest job stores every filing, the universe job picks the point-in-time one. |
| `earnings`, historical (frozen) + forward (weekly) | PASS | `ingest.py:666-721` — 8-K dates from SEC, forward calendar from Finnhub, `source`/`confidence` stored per row as required |
| **Validation — every DESIGN §2.3 rule** | **FAIL (one rule missing)** | `validate_bars()` (`ingest.py:334-431`) implements: `high<low`, `close`/`open` outside range, zero/null volume, >40% move classified against `corporate_actions` (split-explained vs. unexplained-split-like vs. flagged), price < $1, 5+ day identical-close runs. **Missing:** "Missing bar on an NYSE trading day → flag, refetch, mark gap." The `validate_bars` docstring explicitly says this rule "live[s] in `run_validate` instead, which has the full `trading_days` and `bars` history to work against" (`ingest.py:338-342`) — but `run_validate` (`ingest.py:735-803`) only computes `bar_rejects` counts, per-ticker coverage stats, and the Stooq cross-check. It never diffs `bars` against `trading_days` to find gaps. This is a real, unclosed gap against a table-listed acceptance rule, not a stylistic simplification. |
| Duplicate `(ticker, ts, interval)` → upsert, keep latest | PASS | Handled structurally by `db_io.upsert` (`ON CONFLICT DO UPDATE`), not by `validate_bars` — correct per DESIGN §2.3's own framing of this rule as a write-time concern |
| Stooq cross-check on 20-ticker sample | PASS (code) | `ingest.py:761-789` — 20-ticker default sample, >0.5% diff on >1% of bars flags disagreement, matches DESIGN §2.3/§5.8 |
| Idempotency (rerun → identical row counts) | PASS (code + tests) | All writes route through `db_io.upsert`; `tests/integration/test_ingest.py::test_backfill_rerun_is_idempotent` and `test_upsert_is_conflict_safe_for_bars_primary_key` target this directly (unexecuted here, but well-formed) |
| Acceptance: `cscan backfill --tickers TSM,NVDA,AAPL --start 2020-01-01 --through-validate` | PARTIAL | `run_backfill` (`ingest.py:845-873`) runs calendar → tickers → bars → actions → market → validate, stopping at the gate exactly as documented. Never run against a real DB in this audit (forbidden). Code shape matches the acceptance script. |

**Gaps/concerns — Session 5:**
- **Missing-bar-vs-trading-day validation is not implemented anywhere**, despite the code's own docstring claiming it lives in `run_validate`. This is the one DESIGN §2.3 rule not actually wired up. Low urgency for scan()'s current v1 surface, but it is exactly the kind of silent gap CLAUDE.md invariant 4 cares about (a genuinely missing bar looks identical to "no signal fired," and nothing currently flags it).
- `events`, `universe`, `earnings`, `shares_outstanding` tables are empty in the live DB — expected, since the full backfill/nightly chain has never been run end-to-end against real data. Not a code defect, but it means none of the "Stooq agreement," "idempotency," or "validation clean" acceptance lines have been *exercised* against real ingested data, only against synthetic fixtures in (unexecuted) integration tests.

---

## SESSION 6 — Compute jobs and `scan()`: **PARTIAL**

| Criterion | Status | Evidence |
|---|---|---|
| `indicators` job, read/write window asymmetry | PASS | `compute.py:153-219` (`run_indicators`) computes `read_start = target_start - ceil(max_warmup()*1.6 days)` (`compute.py:166`) exactly per DESIGN §4.5, then slices to the write window (`compute.py:113-116`) before upserting. Matches `TESTS.md §5`'s `test_indicator_read_window_expands` shape. |
| `ProcessPoolExecutor(max_workers=14)` | PARTIAL | Parallel path exists and is correctly spawn-safe (`_compute_one_ticker` opens its own connection per CLAUDE.md's platform rule, is a module-level function so it's picklable under spawn) — `compute.py:84-119`, `153-184`. However, **14 is not hardcoded**; `max_workers` is a caller-supplied parameter (CLI `--workers`, default `1`, serial). DESIGN §4.5 pins 14 explicitly. Not wrong, but the number isn't wired as the actual default anywhere, so a plain `cscan indicators` run today is single-threaded, not the specified 14-worker parallel job. |
| Merge `days_to_earnings`, `spx_ret_1d`, VIX in one vectorized pass | PARTIAL | `days_to_earnings` merge is implemented and vectorized per ticker group (`compute.py:122-150`, `_merge_days_to_earnings`). `spx_ret_1d` and VIX (`vix_close`, `vix_pct_252d`) are **not merged into the `indicators` table at all** — they only appear later, joined per-event inside `run_events`/`_build_event_row` (`compute.py:577-578`) from `market_days`, not written onto `indicators` rows. The `indicators` table doesn't carry these two columns (correctly — they aren't in the DESIGN §2.5 `indicators` DDL), so this is not a schema violation, but it means the job's merge step described in BUILD 6.1 ("merge ... in a single vectorized pass after per-ticker computation") is really split: `days_to_earnings` merges into `indicators`, while `spx_ret_1d`/VIX merge only later, per-event, in a different job. Worth flagging so nobody assumes `indicators` rows carry market context. |
| `universe` job, three data hazards | PARTIAL | Shares lag (`compute.py:252-264`) and sector-median fallback (`compute.py:314-344`, correct <5-member fallback to universe median) are implemented correctly. **Revenue growth is a stub returning `None` unconditionally** (`compute.py:299-311`, `_revenue_growth_positive`) — documented honestly in its own docstring as "a documented simplification... Extending the fetcher to pull `us-gaap:Revenues` is the natural follow-up," but it means the fifth ADR-014 criterion never evaluates to `True`/`False`, only `null`, for every ticker, always. `is_tradeable` treats null as failing (per `core/universe.py`), so **every ticker is currently ineligible on this criterion alone** if it's in the required set. |
| `events` job, t−1 read enforced again at job level | PASS | `compute.py:606-654` (`run_events`) reads `prior_ind` strictly from indicator dates `< bar_date` (`compute.py:633-636`), and skips the bar entirely if any of `bb_lower`, `bb_upper`, `k_full` is null (`compute.py:638-641`) — a real second guard on top of `core.signals.detect`'s signature restriction, exactly per DESIGN §4.7 / CLAUDE.md invariant 3. |
| Debounce on `(ticker, signal_date, bound)` tuple, not the dataclass | PASS | `compute.py:646-650` uses `debounce_key(hit)` from `core.signals`, a `seen: set[tuple]` — matches DESIGN §4.7's explicit warning about NaN-field dataclass hashing |
| Cluster tagging | PASS | `compute.py:443-469` (`_tag_clusters`) — deterministic `cluster_id` via SHA-256 truncation (`_deterministic_id`), correctly scoped per `(ticker, side)`, with a documented judgment call on the gap threshold (`ExitParams.max_hold_days`). Covered by 7 unit tests, all passing. |
| `split_key` assigned at event creation, never at query time | PASS | `compute.py:425-431` (`_split_key`) computed once inside `_build_event_row` at write time and stored on the row — matches invariant 5 exactly. No view/query in this session recomputes it. |
| `scan()` contract (ADR 049) | PARTIAL | `compute.py:692-744`. Correct columns, correct filters, and correctly returns `bb_pctb`/`k_full` sourced from the `events` table (i.e., from the t−1 read, not recomputed) — the acceptance criterion's "%B and %K" claim holds. **But** `scan()` additionally joins `indicators i` on `e.ticker = i.ticker AND e.signal_date = i.ts` (`compute.py:734`) to fetch `bb_upper`, `bb_mid`, `bb_lower` for display. That join pulls bar-**t**'s band levels, not the t−1 band that actually produced the signal (`events` doesn't store raw band levels itself, only derived `bb_pctb`). The displayed upper/mid/lower can therefore be inconsistent with the displayed `bb_pctb`/`k_full` on the same row — a real, if narrow, look-ahead-flavored display bug. It does not corrupt `events` or any statistic (those never read this join), but it is exactly the kind of thing CLAUDE.md invariant 3 exists to catch, and no test in the repo checks `bb_upper`/`bb_mid`/`bb_lower` values coming out of `scan()` (the one integration test that checks `scan()` output only asserts on `bb_pctb` and `k_full`). |
| CLI (`cscan scan`, `cscan indicators`, `cscan universe`, `cscan events`) | PASS | `jobs/cli.py:180-235`, `292-331` — all wired, all call the correct `compute.*` functions |
| Warmup assertion (zero nulls after 2010-01-01) | PARTIAL/UNVERIFIED | No standalone unit test asserting this against real 2010+ data (can't — `indicators` table is empty). `tests/integration/test_compute.py::test_zero_nulls_in_required_indicator_columns_after_warmup` covers the shape of this check against a synthetic fixture; matches BUILD §6.6's intent but not literally "for any ticker with continuous coverage" against real data, since none exists yet. |
| Known upsert gap for session 9 | Documented, not yet a problem | `compute.py:13-20` module docstring explicitly flags that `db_io.upsert`'s blind `EXCLUDED`-for-every-column behavior will null out session 9's exit/return columns on a future `run_events` rerun unless session 9 writes a narrower upsert. Correctly called out in advance — this is exactly the kind of thing that should block session 9 rather than surprise it. |

**Gaps/concerns — Session 6:**
- `scan()`'s `bb_upper`/`bb_mid`/`bb_lower` join uses bar t's indicators, not the t−1 indicators the signal was actually detected against (`compute.py:734`). Fix: either store the raw t−1 band levels on `events` at write time, or change the join to `i.ts = (e.signal_date's prior indicator date)`. No existing test would catch a regression here either way.
- `run_universe`'s revenue-growth criterion is a permanent `None` stub — every ticker fails that ADR-014 criterion by default rather than being evaluated. If `is_tradeable`'s default `required` set includes it, this silently makes `in_trade` false for tickers that should qualify. Worth confirming what the default `required` set actually is before trusting any `in_trade` output.
- `ProcessPoolExecutor(max_workers=14)` is described as the job's parallelism in DESIGN/BUILD but is not the default in code (`max_workers=1` unless the caller passes `--workers`). Functionally fine (the spawn-safety guarantee holds either way), but "the job" as specified doesn't run 14-wide unless someone remembers the flag.
- `spx_ret_1d`/VIX merge happens per-event in `run_events`, not per-bar in `run_indicators` as BUILD 6.1's wording implies — not a bug (matches the schema), but worth knowing before assuming `indicators` rows carry market context.

---

## Cross-cutting invariant check

| Invariant | Status | Note |
|---|---|---|
| 2 — one signal implementation | PASS | `compute.py` imports `core.signals.detect`, `core.signals._breach`, `core.signals.debounce_key` only; no second band comparison found anywhere in `jobs/` |
| 3 — t−1 read, enforced again in `events` | PASS in `events`; **narrow miss in `scan()`'s display join** (see above) |
| 4 — never fill/forward-fill; drop + log to `bar_rejects` | PASS | `validate_bars` drops and logs every reject; no `fillna`/`ffill`/`interpolate` found in `ingest.py` or `compute.py` |
| 5/5b — `split_key` at creation, never at query time | PASS | See Session 6 table above |
| 6 — every generated row carries `run_id` (+ `git_sha` via `runs` join) | PASS | `bars`, `bar_rejects`, `indicators`, `events` all stamp `run_id`; `git_sha` lives on `runs` and is joined via `run_id`, per the schema's own design (not stored redundantly on every row) |
| Price series rule (split-adjusted for indicators, total-return for returns) | PASS | `yahoo.py` correctly separates `close`/`adj_close`; `core/indicators.py` (session 2/3 scope, reused here) takes split-adjusted close for bands/stochastic; not re-derived or contradicted anywhere in sessions 4-6 |

---

## Summary of verdicts

- **Session 4 (Fetchers): PASS** — code matches spec closely; the one live-acceptance criterion is unexecuted here and (in the repo) implemented as a mocked-network stand-in rather than literal live tickers.
- **Session 5 (Ingest jobs): PARTIAL** — one DESIGN §2.3 validation rule (missing-bar-vs-trading-day gap detection) is not actually implemented despite code comments claiming it is; everything else checks out.
- **Session 6 (Compute jobs / `scan()`): PARTIAL** — core mechanics (t−1 enforcement, debounce, clustering, split assignment) are solid, but `scan()`'s displayed band levels use the wrong day's indicators, revenue growth is a permanent null stub with universe-eligibility consequences, and the specified 14-way parallelism isn't the actual default.
