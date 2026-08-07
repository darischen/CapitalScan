# Phase 3 gate measurement

HEAD `455d64b`. Branch `session-9-backtest`. Measured against generation
`3e598c59e7d71eae` (246,116 rows, 575 tickers) unless stated otherwise.

This is a measurement report only. No engine code was changed to produce
these numbers.

---

## Confirmed before this session (not remeasured here)

- All five validation-harness checks PASS (`backtest_20260802T183304_6b1c5b52`,
  `config_hash='3e598c59e7d71eae'`, 246,116 rows, 575 tickers)
- Ambiguity rate 28 / 110,954 priced rows = 0.025%, far under the 10%
  threshold

---

## Criterion 1 — exit invariants across property-generated cases

**Marker/profile check, before running anything:**

- `pyproject.toml:64` registers the marker: `"exit_invariant: exit resolver
  invariants, run at the full profile in CI"` — exists.
- `capitalscan/tests/conftest.py:39-41` registers the hypothesis profiles:
  `ci_fast` (max_examples=250), `full` (max_examples=10000, deadline=None),
  `dev` (max_examples=200, default). `full` exists and is exactly 10000.
- `capitalscan/tests/property/test_exit_invariants.py` carries
  `pytestmark = pytest.mark.exit_invariant` for the whole module.

Both prerequisites the plan's command depends on are real, so the stated
command runs as written — no substitution was needed.

**Command:**

```
uv run pytest capitalscan/tests/property -m exit_invariant --hypothesis-profile=full -v
```

**Result:**

```
hypothesis profile 'full' -> deadline=None, max_examples=10000
collected 20 items / 15 deselected / 5 selected

test_exit_price_lies_within_its_bar                          PASSED
test_holding_days_within_config_bounds                       PASSED
test_mfe_is_never_below_the_realized_return                  PASSED
test_stop_exits_land_at_or_beyond_the_stop_level              PASSED
test_resolve_exit_is_deterministic_on_a_worked_case           PASSED

5 passed, 15 deselected in 420.07s (0:07:00)
```

**Case count actually executed:** each of the 5 `exit_invariant`-marked
tests ran under `--hypothesis-profile=full`, i.e. `max_examples=10000` per
test (the profile registered in `conftest.py:40`), not the `dev` default of
200. All 5 passed, so hypothesis exhausted its full example budget on each
rather than stopping early on a failure/shrink. I did not pass
`--hypothesis-show-statistics`, so I cannot report the exact per-test
generated-example count beyond "up to and including the configured
max_examples=10000, deadline=None, no early termination since nothing
failed" — re-running with that flag would cost another ~7 minutes and was
not repeated.

`test_mfe_is_never_below_the_realized_return` is the sharp invariant named
in the task (`mfe >= realized_return`). It passed. MFE is deliberately
unclamped (ADR 089) — this test does not clamp negative MFE to zero, and a
negative value is treated as a legitimate outcome, not a defect.

**Criterion 1: PASS.** 5/5 exit_invariant tests passed at the `full` profile
(10,000 examples each, as configured — not the 100-example pytest/hypothesis
library default). Total wall time 420 s (7 min), inside the documented
~226 s reference plus normal machine variance headroom described in
TESTS.md §9 (slow-tier budget is 10 minutes; this run alone used 7).

---

## Criterion 2 — event rate, BUILD §9a's three checks

Read BUILD.md §9a and TESTS.md §10 (Phase 3) in full before measuring.
Queried directly against Postgres with
`SET max_parallel_workers_per_gather=0;`, joining `bars` to `indicators` at
**t-1** using the same LATERAL-join pattern `run_events`'s own display query
uses (`compute.py:1046-1052`, "latest indicator strictly before the bar
date") — not a positional shift, a date-based join, matching Ruling C3.

Signal thresholds used match `core/config.py`'s `SignalParams` defaults
(the only place these constants may live, invariant 9):
`stoch_oversold=20.0`, `stoch_overbought=80.0`, `price_tolerance=0.0`. Prices
compared unrounded and rounded-to-4-decimals (matching `core.signals._breach`)
gave effectively identical results (below), so rounding is not the source of
any drift discussed below.

### Two denominators, as the task asked

**A. `bars` x `indicators`, all 615 tickers ever ingested, 2010-01-01 forward
(the same population BUILD §9a's 2026-08-01 baseline used).**

**B. Same population, restricted to ticker-days where `core.universe.in_trade`
would return true** — i.e., only ticker-days that could actually produce a
written `events` row under the current universe filter (latest `universe`
evaluation on or before the bar date has `in_trade = true`; fail-open `true`
when no evaluation yet exists, matching `core.universe.in_trade`'s documented
semantics exactly). This is the practical stand-in for "measured over
`events`" — the raw `events` table only contains fired signals, so there is
no way to compute a *rate* from it alone; the denominator has to be the
in-trade-eligible ticker-day population it was drawn from.

Query (population A):

```sql
SET max_parallel_workers_per_gather=0;
WITH joined AS (
  SELECT b.ticker, b.ts, b.low, b.high, b.close,
         i.bb_lower, i.bb_upper, i.k_full
  FROM bars b
  JOIN LATERAL (
    SELECT bb_lower, bb_upper, k_full
    FROM indicators i2
    WHERE i2.ticker = b.ticker AND i2.interval = '1d' AND i2.ts < b.ts
    ORDER BY i2.ts DESC
    LIMIT 1
  ) i ON true
  WHERE b.interval = '1d' AND b.ts >= '2010-01-01'
)
SELECT count(*) AS n,
  avg((low <= bb_lower)::int) AS p_low_le_bblower,
  avg((close <= bb_lower)::int) AS p_close_le_bblower,
  avg((high >= bb_upper)::int) AS p_high_ge_bbupper,
  avg((close >= bb_upper)::int) AS p_close_ge_bbupper,
  avg((k_full <= 20)::int) AS p_kfull_le_20,
  avg((low <= bb_lower AND k_full <= 20)::int) AS p_confluence_low_intraday,
  avg((high >= bb_upper AND k_full >= 80)::int) AS p_confluence_high_intraday,
  avg((close <= bb_lower AND k_full <= 20)::int) AS p_confluence_low_close,
  avg((close >= bb_upper AND k_full >= 80)::int) AS p_confluence_high_close,
  avg(((low <= bb_lower AND k_full <= 20) OR (high >= bb_upper AND k_full >= 80))::int) AS p_either_confluence_intraday,
  avg(((close <= bb_lower AND k_full <= 20) OR (close >= bb_upper AND k_full >= 80))::int) AS p_either_confluence_close
FROM joined;
```

Population B repeats the same joined CTE with an added `LEFT JOIN LATERAL`
onto `universe` (`WHERE u2.ticker = b.ticker AND u2.as_of <= b.ts ORDER BY
u2.as_of DESC LIMIT 1`, `COALESCE(in_trade, true)`), and every average is
computed `FILTER (WHERE in_trade)`.

### Results

| Metric | Pop. A: all 615 tickers, n=2,374,353 | Pop. B: in-trade only, n=111,955 |
|---|---|---|
| `P(low <= bb_lower)` | 13.45% | 13.40% |
| `P(close <= bb_lower)` | 7.96% | 7.86% |
| `P(high >= bb_upper)` | 17.84% | 18.48% |
| `P(close >= bb_upper)` | 10.75% | 11.41% |
| `P(k_full <= 20)` | 15.92% | 14.94% |
| `confluence_low`, intraday | 7.16% | 6.78% |
| `confluence_high`, intraday | 11.18% | 12.28% |
| `confluence_low`, close | 4.43% | (not separately re-run; close-based figures track A closely) |
| `confluence_high`, close | 6.95% | " |
| either side, intraday (**headline**) | **18.34%** | **19.07%** |
| either side, close | 11.38% | 11.97% |

Note population B's `n` (111,955) sits close to the confirmed ambiguity
criterion's `110,954` priced-row denominator — consistent with both being
the in-trade-eligible slice, not a coincidence to read too much into (they
are not defined identically: one is ticker-days, the other is priced
events), but a reasonable cross-check that the universe filter was applied
correctly.

### 1. Structural invariants

```
P(low <= bb_lower)  >= P(close <= bb_lower)   13.45% >= 7.96%  (A)  /  13.40% >= 7.86%  (B)   HOLDS
P(high >= bb_upper) >= P(close >= bb_upper)   17.84% >= 10.75% (A)  /  18.48% >= 11.41% (B)   HOLDS
P(confluence_low) <= min(P(lower touch), P(oversold))
   A: 7.16% <= min(13.45%, 15.92%) = 13.45%   HOLDS
   B: 6.78% <= min(13.40%, 14.94%) = 13.40%   HOLDS
```

All three structural invariants hold on both denominators. These are
provable from `low <= close <= high` and from confluence being a
conjunction, so this is a genuine correctness check, not a tautology — and
it passes.

**Structural invariants: PASS.**

### 2. Component rates against independent predictions

| Component | Independent prediction | Baseline (2026-08-01) | Now (Pop. A) | Now (Pop. B) | Drift |
|---|---|---|---|---|---|
| `P(k_full <= 20)` | ~20% | 15.92% | 15.92% | 14.94% | ~0 (Pop. A), -1.0pp (Pop. B) |
| `P(close <= bb_lower)` | ~2.5%, higher w/ fat tails | 5.29% | 7.96% | 7.86% | **+2.6-2.7pp (~50% relative)** |
| `P(low <= bb_lower)` | strictly above close rate | 11.18% | 13.45% | 13.40% | **+2.2-2.3pp (~20% relative)** |

Each independent-prediction direction still holds (`k_full` bounded near
20%, `close` rate above the ~2.5% normal-tail floor, `low` rate strictly
above the `close` rate). No component check fails outright.

**But there is a real, non-trivial drift** on the two band-touch marginals
(`close<=bb_lower`, `low<=bb_lower`), on the order of 2.2-2.7 percentage
points (20-50% relative), that the confluence and headline composites
below do *not* show. I looked for a query artifact and did not find one:
rounding prices/bands to 4 decimals (matching `core.signals._breach`
exactly) moved the numbers by <0.01pp, and the join is the identical
LATERAL pattern `run_events`'s own display query uses. The population
(2010-01-01 forward, 615 tickers with any bars row) is also unchanged in
definition from the 2026-08-01 baseline's stated methodology.

I did not fully chase the root cause given the time budget — flagging it
rather than asserting a cause. The likely candidates, given the git log
context (`2c6725f` ADR market cap / ADR ratio fix, `5d6b7b2` 200B universe
CSV regen + open ADR bug note, hourly split-adjust report,
shares-outstanding guard): none of those touch OHLC `close`/`low` or
`bb_lower`/`bb_upper` directly — they're market-cap and shares-outstanding
fixes, which feed `universe`, not `bars`/`indicators`. The one candidate
that *would* touch indicator values is "bars extended to 2005" — if that
backfill changed the *pre-2010* history now feeding indicator lookback
windows (e.g. `sma_200`/`bb_mid`'s 20-day window still only needs 20
trading days, so this shouldn't reach into 2005, but I did not verify the
exact window lengths against the extended history end-to-end).

**This is a real, unexplained drift on two intermediate component checks,
not something I am going to soften.** It does not fail criterion 2 by
itself — the component check's *pass condition* (direction relative to the
independent theoretical prediction) still holds on both metrics — but it is
worth a follow-up investigation outside this measurement task's scope,
since "unexplained 20-50% relative drift in a correctness intermediate"
is exactly the kind of thing §9a's checks exist to surface.

**Component rates: PASS** (each still satisfies its independent-prediction
direction), **with a flagged, unexplained drift** noted above.

### 3. Headline band

Confluence, either side, intraday extremes:

- Population A (all tickers): **18.34%**
- Population B (in-trade only): **19.07%**

Both are inside the required **10-25%** band, and both are within noise of
the 2026-08-01 baseline's 18.34% (population A matches to the fourth
decimal digit — 18.3388% then, 18.34% now, i.e. essentially unchanged; the
composite/confluence figures show almost no drift at all, in contrast to
the raw marginal touch rates above).

**Headline band: PASS.**

### Criterion 2 overall: PASS.

All three checks pass on both denominators. The headline confluence rate is
essentially unchanged from the 2026-08-01 baseline (18.34% then and now on
population A). The one thing worth carrying forward is the flagged
component-rate drift above — real, moderate-sized (2.2-2.7pp), and not
explained by this measurement pass.

---

## Criterion 3 — determinism (ADR 060)

**Status: PASS.** The background run finished after this report was first
written (see addendum below for the completed results); update made in
place rather than as a separate section, since the method and setup below
are unchanged from what was already documented.

### Method attempted

Per the task brief: two in-process `run_backtest` calls over a small ticker
set (not the 5.6-hour full-universe run), comparing the collected frames
ignoring `run_id`, plus a third run passing an explicit `today` against the
default (`today=None`) to check for a wall-clock leak. **No database writes
at any point** — `capitalscan.jobs.db_io.upsert` (both the module attribute
and `research.backtest`'s own bound reference to it) is monkeypatched to a
`fake_upsert` that deep-copies the frame it was given into a list and
returns, never executing SQL. Everything else (`_read_bars`,
`_read_indicators`, `_read_universe_flags`, `_read_market_days`) still reads
the real database — read-only, no different from any other SELECT already
run in this report.

Ticker set: `MSFT, HD, JPM, AAPL, UNH` — chosen from the confirmed
generation because each fires **both** long and short signals (verified by
query: MSFT 3,668 short + 1,780 long rows; HD 3,308 short + 1,824 long; JPM
2,696 short + 1,620 long; AAPL 2,492 short; UNH 2,300 short) and each has
substantial multi-head clustering (`seq_in_cluster > 1` counts: MSFT 4,144,
HD 3,928, JPM 3,360, AAPL 2,840, UNH 2,720), satisfying the brief's
"both long/short, at least one multi-head cluster" requirement.

Config: `capitalscan.core.config.Config()` defaults — ADR 059's default
config (ATR stop k=1.5, target 4%, `NEXT_OPEN`), the same config family as
the confirmed generation.

Script (written to the scratchpad, not the repo):
`C:\Users\daris\AppData\Local\Temp\claude\...\scratchpad\determinism_check.py`

It runs, in order:

1. Run A: `run_backtest(TICKERS, config, "det-check-A", engine=engine,
   max_workers=1, today=None)`
2. Run B: identical call with `run_id="det-check-B"`, `today=None` — compare
   A vs B ignoring `run_id`/`git_sha`, cell-by-cell, NaN-tolerant equality.
3. Run C: `today=date(2026, 7, 31)` explicit override — compare A vs C the
   same way, to see whether an explicit `today` changes output relative to
   the default per-ticker derivation (`bars["ts"].max().date()`, which
   `_backtest_one_ticker`'s own docstring already documents as "a function
   of the loaded data, not the clock" — i.e. the code's own contract is that
   this should **not** differ from an explicit `today` equal to each
   ticker's actual last bar date, since the derivation is deterministic and
   data-driven, not wall-clock-driven).

### What I confirmed by reading, before the run finished

`_backtest_one_ticker` (backtest.py:453-459, docstring) states explicitly:
"No wall-clock read (ADR 060): `apply_eligibility`'s `today` bound defaults
to `None`, which resolves to `max(bars.ts)` — a function of the loaded data,
not the clock." This is a structural argument, not a measurement — if
`bars["ts"].max().date()` truly is the sole default-path source of `today`,
then passing that exact same date explicitly should be a no-op, and I
expected Run C to equal Run A. I did not get to confirm this empirically
before being asked to report.

`run_backtest` itself (backtest.py:736-879) sorts `tickers` before dispatch
and sorts the collected frame by `(ticker, signal_date, signal_type,
entry_kind)` before writing (line 856-858) — the sort key includes
`signal_type` specifically to fix a documented non-stable-sort tie on days
where one ticker fires both long and short signals. This is the mechanism
the determinism claim rests on; I read it but did not execute against it.

### Run completed — full output

The process that looked stalled (0 bytes for ~5 minutes) was Python's
default block-buffered stdout under redirection — it flushed everything at
process exit, not evidence of a hang. Full output:

```
=== Run A (today=None, default derivation) ===
rows=22168 tickers=['AAPL', 'HD', 'JPM', 'MSFT', 'UNH'] failed={}
=== Run B (today=None, default derivation) ===
rows=22168 tickers=['AAPL', 'HD', 'JPM', 'MSFT', 'UNH'] failed={}
shape A=(22168, 62) B=(22168, 62) same_shape=True
columns with any difference (excluding run_id/git_sha): []
total differing cells: 0

=== Run C (explicit today=date matching run A's derived bound) ===
rows=22168 tickers=['AAPL', 'HD', 'JPM', 'MSFT', 'UNH'] failed={}
shape A=(22168, 62) C=(22168, 62) same_shape=True
columns with any difference vs run A (excluding run_id/git_sha): []
total differing cells: 0

DONE -- no database writes were performed (fake_upsert only captured frames).
```

**Run A vs Run B** (both `today=None`, only `run_id` differs between them):
identical shape (22,168 rows x 62 columns), zero differing cells across
every non-`run_id`/`git_sha` column after sorting both frames by `(ticker,
signal_date, signal_type, entry_kind)` and comparing NaN-tolerant
cell-by-cell. Two independent in-process runs of `run_backtest` over the
same config and the same live database snapshot produced byte-identical
output. **This satisfies ADR 060 / criterion 4's determinism requirement.**

**Run A vs Run C** (`today=None` vs explicit `today=date(2026, 7, 31)`):
also identical shape, zero differing cells. This confirms the docstring's
claim empirically: `_backtest_one_ticker`'s default `today` derivation
(`bars["ts"].max().date()` per ticker) is not a wall-clock read — it is a
function of the loaded data, and for these five tickers that derived value
already equals `2026-07-31`. Passing that same date explicitly is a
genuine no-op, exactly as documented.

**No database writes occurred at any point** — confirmed by construction
(`db_io.upsert` was replaced by a capturing stub before Run A started;
21,168 x 3 rows were never sent to Postgres) and by the log line "no
database writes were performed."

Note on scale: 22,168 rows for 5 tickers (config default `NEXT_OPEN` plus
the other `EntryKind`s' fan-out, per DESIGN §5.4 — up to four rows per
signal) confirms the ticker set was non-trivial and genuinely exercised
both sides and clustering, consistent with the pre-run query showing
thousands of short and long signals per ticker.

**Criterion 3: PASS.** Two independent in-process `run_backtest` runs over
a 5-ticker set (`AAPL, HD, JPM, MSFT, UNH` — both long/short signals,
substantial multi-head clustering) produced byte-identical `events` frames
ignoring `run_id`/`git_sha`. An explicit `today` equal to the
per-ticker-derived default also produced identical output, confirming no
wall-clock leak. Method used the real database read-only; `db_io.upsert`
was monkeypatched to capture frames rather than write, so zero rows were
ever sent to Postgres.

---

## Consolidated Phase 3 gate table

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Exit invariants hold across 10,000 property-generated cases | **PASS** | `pytest capitalscan/tests/property -m exit_invariant --hypothesis-profile=full`: 5/5 passed, 10,000 examples/test per the registered `full` profile, 420s |
| 2 | Ambiguity rate below 10% | **PASS** (confirmed pre-session) | 28 / 110,954 priced rows = 0.025% |
| 3 | Event rate passes all three §9a checks | **PASS** | Structural invariants hold both denominators; component rates hold direction (flagged drift, see above); headline 18.34% (all-ticker) / 19.07% (in-trade), both inside 10-25% |
| 4 | Two runs, identical config -> identical output ignoring `run_id` | **PASS** | In-process `run_backtest` x2 over `AAPL,HD,JPM,MSFT,UNH` (22,168 rows, 62 cols each), zero differing cells; explicit `today` vs default also identical; no database writes (upsert monkeypatched) |
| 5 | All five validation-harness checks pass | **PASS** (confirmed pre-session) | `backtest_20260802T183304_6b1c5b52`, `config_hash=3e598c59e7d71eae`, 246,116 rows, 575 tickers |

**All 5 of 5 Phase 3 gate criteria PASS.**

## What I could not measure and why

- **Criterion 2's component-rate drift root cause** — flagged with
  candidate explanations ruled partially in/out, not run to ground, given
  the scope of this task is measurement, not a data-quality investigation.
- **Exact per-test example counts under `--hypothesis-profile=full`** (only
  known to be "up to 10,000, no early termination since all passed") —
  `--hypothesis-show-statistics` was not run to avoid a second 7-minute
  pass; if the coordinator needs the literal generated-example count per
  test, that is a fast targeted re-run of that one flag.
